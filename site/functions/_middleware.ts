/**
 * The gate in front of everything.
 *
 * A Pages middleware at the root of `functions/` runs on every request to the project, including
 * the ones that would otherwise be served straight from static assets — so this is the only place
 * a gate has to exist for the whole deployment to be behind it. Preview deployments included:
 * they are the same project, the same Functions, and every `*.pages.dev` URL the project ever
 * mints goes through this file.
 *
 * IT FAILS CLOSED, and that is the important property. With neither gate configured this serves
 * nothing at all, which is what makes it safe to deploy before the protection is switched on — a
 * site that is briefly public is not a smaller version of the problem, it is the problem. There
 * is no ordering to get right, because there is no order in which this is open.
 *
 * Two gates, and Access wins when both are set:
 *
 *   ACCESS_DOMAIN + ACCESS_AUD   Cloudflare Access. Access itself sits at the edge in front of
 *                                this Worker and does the login; the plugin here verifies the JWT
 *                                it leaves behind, so a request that somehow reaches the origin
 *                                without one is still refused. Defence in depth rather than the
 *                                lock itself. Needs a Zero Trust application over this hostname.
 *   SITE_PASSWORD                HTTP Basic auth. No dashboard, no account setup, works the
 *                                moment the secret is set, and iOS Safari prompts for it like any
 *                                other password. This is the one to test with.
 */
import cloudflareAccessPlugin from "@cloudflare/pages-plugin-cloudflare-access";

interface Env {
  ACCESS_DOMAIN?: string;
  ACCESS_AUD?: string;
  SITE_PASSWORD?: string;
}

const REALM = 'Basic realm="petrograph", charset="UTF-8"';

function ask(): Response {
  return new Response("Authentication required.\n", {
    status: 401,
    headers: { "WWW-Authenticate": REALM, "content-type": "text/plain; charset=utf-8" },
  });
}

/** Constant time, so the comparison cannot be turned into an oracle for the password. */
function same(given: string, expected: string): boolean {
  const a = new TextEncoder().encode(given);
  const b = new TextEncoder().encode(expected);
  // Lengths differ: answer no, but compare something of equal length anyway so the timing of
  // this branch does not itself leak the length.
  if (a.byteLength !== b.byteLength) {
    crypto.subtle.timingSafeEqual(b, b);
    return false;
  }
  return crypto.subtle.timingSafeEqual(a, b);
}

function basic(context: EventContext<Env, string, unknown>, expected: string): Response | Promise<Response> {
  const header = context.request.headers.get("Authorization") ?? "";
  const [scheme, encoded] = header.split(" ");
  if (scheme !== "Basic" || !encoded) return ask();
  let decoded: string;
  try {
    decoded = atob(encoded);
  } catch {
    return ask();
  }
  // Everything after the first colon. A password may contain colons; a username may not.
  const colon = decoded.indexOf(":");
  const given = colon < 0 ? "" : decoded.slice(colon + 1);
  return same(given, expected) ? context.next() : ask();
}

export const onRequest: PagesFunction<Env> = async (context) => {
  const { ACCESS_DOMAIN, ACCESS_AUD, SITE_PASSWORD } = context.env;

  if (ACCESS_DOMAIN && ACCESS_AUD) {
    return cloudflareAccessPlugin({ domain: ACCESS_DOMAIN, aud: ACCESS_AUD })(context);
  }
  if (SITE_PASSWORD) {
    return basic(context, SITE_PASSWORD);
  }
  return new Response(
    "This deployment has no gate configured, so it serves nothing.\n" +
      "Set SITE_PASSWORD, or ACCESS_DOMAIN and ACCESS_AUD, and redeploy.\n",
    { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } },
  );
};
