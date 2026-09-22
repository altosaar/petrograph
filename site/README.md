# site — a password-gated one-off host for the synthetic pages

Three of the four pages in `synthetic/sessions/` cannot be opened from a file on iOS. DuckDB-WASM
spawns a Web Worker and instantiates a ~30 MB module, both fetched cross-origin; a page opened
from `file://` (or Edge's `edge://external-file`) has an **opaque origin**, so WebKit blocks both
and the page sits on "loading duckdb-wasm" forever. Every browser on iOS is WebKit, so there is no
browser to switch to. Served over https the same page works untouched.

`microlite-waffle.html` is the exception and always was: no scripts, no imports, the SVG inline.
That one opens from a file anywhere, and is worth keeping as the control.

## Synthetic only, by construction

`tools/site_build.py` reads `synthetic/` and there is no flag that makes it read anywhere else.
Before it copies anything it checks what it is about to publish, against every note title in the
real vault. **Real note titles are absolute**: any page containing one stops the build, and there
is no flag that overrides it. A hosted URL is reachable, cached, and outlives the intention
behind it, and a title is the one thing on these pages a reader could recognise.

That is the only check, and it is the only one that was ever about privacy. There used to be a
second, against a deny list of subjects; it went when the deny list did. What it stopped was a
synthetic bereavement being described as one — an editorial question, not a privacy one. See
`synthetic/README.md`.

## The gate fails closed

`functions/_middleware.ts` runs on every request to the project — static assets included, preview
deployments included. With **neither** gate configured it serves nothing at all, which is what
makes it safe to deploy before the protection is switched on. A site that is briefly public is not
a smaller version of the problem; it is the problem.

Two gates, Access winning when both are set:

| secret | what it is |
| --- | --- |
| `SITE_PASSWORD` | HTTP Basic auth. No dashboard, no account setup, works the moment it is set, and iOS Safari prompts for it like any other password. **Start here.** |
| `ACCESS_DOMAIN` + `ACCESS_AUD` | Cloudflare Access. Access sits at the edge and does the login; the plugin here verifies the JWT it leaves behind, so a request reaching the origin without one is still refused — defence in depth rather than the lock itself. Needs a Zero Trust application over this hostname. |

## Deploy

```sh
just site-build                       # assemble site/public from synthetic/
just site-password                    # generate a password and set it as a secret
just site-deploy                      # push it
```

The first deploy creates the project. Until `SITE_PASSWORD` is set every request gets a 503, so
the order above is the safe one either way.

### Upgrading to Access

1. Zero Trust → Access → Applications → Add a **self-hosted** application over the project's
   hostname.
2. Add a policy. Access has no shared password: the equivalents are a one-time PIN to an allowed
   email list, or an identity provider.
3. Copy the application's **Application Audience (AUD) tag** and your team domain.
4. `wrangler pages secret put ACCESS_AUD` and `ACCESS_DOMAIN` (`https://<team>.cloudflareaccess.com`).

Access then takes precedence over the password automatically — no redeploy needed beyond the
secrets.

## Taking it down

```sh
just site-destroy
```

It is a one-off. Deleting the project is the honest end state, and the pages are regenerable from
`just synth` whenever they are wanted again.
