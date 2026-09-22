#!/usr/bin/env -S npx --yes tsx@4
/**
 * Headless Oura summary — the same note the plugin's ribbon icon writes, without Obsidian.
 *
 * Exists so the weekly pipeline can produce the note unattended. It is deliberately a
 * thin shell around the very same `buildDays` and `renderNote` the plugin calls, pinned
 * here as the `vendor/obsidian-oura-metrics` submodule: if the metric selection or the
 * rendering ever diverged between the app and the CLI, the note that reaches a model
 * would stop being the note you reviewed by eye, which is the whole basis for trusting it.
 *
 * The only thing it reimplements is the HTTP call, because Obsidian's `requestUrl` does
 * not exist here — hence `fetchTransport` below, injected into the plugin's OuraClient.
 *
 * Usage:
 *   ./tools/oura_metrics.ts --days 7 --out oura.md
 *   ./tools/oura_metrics.ts --days 28 > note.md
 *   ./tools/oura_metrics.ts --days 7 --baseline-weeks 0     # no comparison period
 *
 * `--days` is the window that gets reported day by day. Three further weeks are
 * fetched behind it and become the baseline every mean, Δ and deviation is read
 * against, so a week is compared with the weeks before it rather than with itself.
 *
 * The token is an OAuth access token: Oura retired personal access tokens in December 2025,
 * and the plugin (0.2.0 on) runs the consent flow itself. This never runs a flow of its own —
 * it borrows the token the plugin stored, so connecting once in Obsidian serves both. The
 * implicit grant issues no refresh token, so when it lapses (~30 days) the fix is Reconnect
 * in the plugin's settings, and this says so rather than surfacing a bare 401.
 *
 * Token resolution, first hit wins:
 *   --token <t>                     explicit
 *   $OURA_TOKEN                     environment
 *   <vault>/.obsidian/plugins/oura-metrics/data.json    `accessToken`, what the plugin stored
 *
 * The vault comes from --vault, then $OBSIDIAN_VAULT, then ~/notes — the same default
 * vault_corpus.py and vault_snapshot.sh use, so the weekly pipeline needs no setup here.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';

import {
	type DailyActivity,
	type DailyReadiness,
	type DailyScore,
	type OuraTransport,
	type SleepPeriod,
	OuraApiError,
	OuraClient,
	isoDate,
} from '../vendor/obsidian-oura-metrics/src/oura';
import {
	DEFAULT_BASELINE_WEEKS,
	buildDays,
	splitWindow,
} from '../vendor/obsidian-oura-metrics/src/metrics';
import { isExpired } from '../vendor/obsidian-oura-metrics/src/oauth';
import { renderNote } from '../vendor/obsidian-oura-metrics/src/render';

const DAY_MS = 86_400_000;
const DEFAULT_THRESHOLD = 1.5;
const DEFAULT_VAULT = join(homedir(), 'notes');
const RECONNECT = "Open Obsidian → Settings → Oura Metrics and click Reconnect.";

interface Options {
	days: number;
	/** Weeks of history fetched before the window, to compare it against. 0 disables it. */
	baselineWeeks: number;
	out: string | null;
	token: string | null;
	vault: string | null;
	threshold: number;
}

/** `fetch`-backed transport. The plugin supplies its own from `requestUrl`. */
const fetchTransport: OuraTransport = async ({ url, headers }) => {
	const response = await fetch(url, { headers });
	// Read the body before checking status: the error branches in OuraClient don't use
	// it, but a 4xx with a JSON body would otherwise leave the stream unconsumed.
	const text = await response.text();
	let json: unknown = null;
	try {
		json = text ? JSON.parse(text) : null;
	} catch {
		json = null;
	}
	return { status: response.status, json };
};

function fail(message: string): never {
	process.stderr.write(`oura-metrics: ${message}\n`);
	process.exit(1);
}

function parseArgs(argv: string[]): Options {
	const options: Options = {
		days: 7,
		baselineWeeks: DEFAULT_BASELINE_WEEKS,
		out: null,
		token: null,
		vault: null,
		threshold: DEFAULT_THRESHOLD,
	};

	for (let i = 0; i < argv.length; i++) {
		const arg = argv[i];
		const next = () => {
			const value = argv[++i];
			if (value === undefined) fail(`${arg} needs a value`);
			return value;
		};
		switch (arg) {
			case '--days':
				options.days = Number(next());
				if (!Number.isInteger(options.days) || options.days < 1) {
					fail('--days must be a positive integer');
				}
				break;
			case '--baseline-weeks':
				options.baselineWeeks = Number(next());
				if (!Number.isInteger(options.baselineWeeks) || options.baselineWeeks < 0) {
					fail('--baseline-weeks must be a non-negative integer');
				}
				break;
			case '--out':
				options.out = next();
				break;
			case '--token':
				options.token = next();
				break;
			case '--vault':
				options.vault = next();
				break;
			case '--threshold':
				options.threshold = Number(next());
				if (!Number.isFinite(options.threshold)) fail('--threshold must be a number');
				break;
			case '-h':
			case '--help':
				process.stdout.write(
					'Usage: oura_metrics.ts [--days 7] [--baseline-weeks 3] [--out FILE] ' +
						'[--token T] [--vault DIR] [--threshold 1.5]\n',
				);
				process.exit(0);
			default:
				fail(`unknown argument: ${arg}`);
		}
	}
	return options;
}

function readToken(options: Options): string {
	if (options.token) return options.token;
	if (process.env.OURA_TOKEN) return process.env.OURA_TOKEN;

	const vault = options.vault ?? process.env.OBSIDIAN_VAULT ?? DEFAULT_VAULT;
	const dataPath = join(resolve(vault), '.obsidian', 'plugins', 'oura-metrics', 'data.json');
	let raw: string;
	try {
		raw = readFileSync(dataPath, 'utf8');
	} catch {
		fail(
			`no token, and no plugin data at ${dataPath}. Install Oura Metrics in that vault ` +
				'and connect it, point --vault (or $OBSIDIAN_VAULT) at the vault that has it, ' +
				'or pass --token.',
		);
	}
	let data: { accessToken?: unknown; tokenExpiresAt?: unknown };
	try {
		data = JSON.parse(raw) as typeof data;
	} catch {
		fail(`${dataPath} is not valid JSON`);
	}
	const token = data.accessToken;
	if (typeof token !== 'string' || !token) {
		fail(`the plugin in ${vault} is not connected to Oura. Open Obsidian → Settings → ` +
			'Oura Metrics and click Connect.');
	}
	const expiresAt = typeof data.tokenExpiresAt === 'number' ? data.tokenExpiresAt : 0;
	if (isExpired(expiresAt, Date.now())) {
		fail(`the Oura connection expired ${new Date(expiresAt).toISOString().slice(0, 10)}. ${RECONNECT}`);
	}
	return token;
}

function windowLabel(days: number): string {
	if (days === 7) return '7 days';
	if (days === 14) return '2 weeks';
	if (days === 28) return '4 weeks';
	return `${days} days`;
}

function baselineLabel(weeks: number): string {
	return weeks === 1 ? 'the previous week' : `the previous ${weeks} weeks`;
}

async function main(): Promise<void> {
	const options = parseArgs(process.argv.slice(2));
	const token = readToken(options);

	const now = new Date();
	// Oura's `day` is the wake day, so a night is reported on the morning it ends.
	// Reaching one day past today costs nothing and avoids clipping last night.
	const end = isoDate(new Date(now.getTime() + DAY_MS));
	// The window that gets reported day by day starts here; the fetch reaches further
	// back, and everything before this date becomes the baseline it is compared with.
	// `days - 1` because today is one of them: --days 7 means today and six before it,
	// so a "week" against three "weeks" really is 7 days against 21.
	const windowStart = isoDate(new Date(now.getTime() - (options.days - 1) * DAY_MS));
	const start = isoDate(
		new Date(now.getTime() - (options.days - 1 + options.baselineWeeks * 7) * DAY_MS),
	);

	const client = new OuraClient(token, fetchTransport);
	const [sleep, dailySleep, dailyActivity, dailyReadiness] = await Promise.all([
		client.collect<SleepPeriod>('sleep', start, end),
		client.collect<DailyScore>('daily_sleep', start, end),
		client.collect<DailyActivity>('daily_activity', start, end),
		client.collect<DailyReadiness>('daily_readiness', start, end),
	]);

	const fetched = buildDays({ sleep, dailySleep, dailyActivity, dailyReadiness }, isoDate(now));
	const { window: days, baseline } = splitWindow(fetched, windowStart);
	if (days.length === 0) {
		fail(`Oura returned no data for ${windowStart}..${end}. Is the ring syncing?`);
	}

	// No prompt template: the CLI's output is an attachment inside a larger
	// bundle that carries its own instructions, so a second set would conflict.
	const markdown = renderNote(days, {
		windowLabel: windowLabel(options.days),
		generatedAt: now,
		threshold: options.threshold,
		baseline,
		baselineLabel: baselineLabel(options.baselineWeeks),
	});

	if (options.out) {
		writeFileSync(options.out, markdown);
		process.stderr.write(
			`oura-metrics: ${days.length} days over ${baseline.length} baseline days → ${options.out}\n`,
		);
	} else {
		process.stdout.write(markdown);
	}
}

main().catch((err: unknown) => {
	// A 401 on a token the plugin stored is almost always a revoked or lapsed grant, and the
	// cure lives in Obsidian, not here.
	if (err instanceof OuraApiError) fail(err.status === 401 ? `${err.message} ${RECONNECT}` : err.message);
	fail(err instanceof Error ? err.message : String(err));
});
