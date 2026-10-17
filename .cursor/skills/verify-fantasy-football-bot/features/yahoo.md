# Yahoo league read

## Sub-features

- `auth-url` — print an OAuth URL
- `connect` — exchange a code and store a refresh token
- `leagues` — NFL leagues for the connected user
- `get` — JSON GET of one fantasy path

## How to get to it (user POV)

Yahoo is untested. The manager cannot use it until the owner is approved for Fantasy API access. The agent does not ask for the client secret or auth code in chat. `connect` stores the refresh token in a mode-0600 file and does not print it.

## Driving it with unittest

Do not call Yahoo. Mark the feature untested. The help text is the check that the mark is still there:

```bash
set -euo pipefail
python3 league_read.py yahoo --help | grep -q -i untested
```

`tests.test_league_read.TestYahoo` patches HTTP. A passing run does not mean Yahoo works. Leave it untested in the PR evidence.

## Gotchas

- `auth-url` rejects `oob`, localhost, and non-https redirects, and it does not itself perform a token request. Still do not run it with a real client id during verification.
- `connect` writes `yahoo_token.json`. The unittest uses a temporary directory. A hand run would write under the config directory.
- A 403 that says the application is not authorized is `yahoo_not_approved`, and the payload includes `untested`.
- `yahoo get` rejects a full URL and a `..` path.
