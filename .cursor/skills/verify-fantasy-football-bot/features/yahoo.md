# Yahoo league read

`league_read.py yahoo` would build an OAuth URL, store a refresh token, list NFL leagues, and GET one fantasy path. Yahoo is untested. The manager cannot rely on it until the owner is approved for Fantasy API access.

## Sub-features

- `auth-url` prints an OAuth URL.
- `connect` exchanges a code and stores a refresh token.
- `leagues` lists NFL leagues for the connected user.
- `get` is a JSON GET of one fantasy path.

## How to get to it (user POV)

The agent does not ask for the client secret or auth code in chat. `connect` stores the refresh token in a mode-0600 file and does not print it. The commands, if they were approved, would be `yahoo auth-url`, `yahoo connect`, `yahoo leagues`, and `yahoo get PATH`.

## Driving it with the CLI

Preconditions:

- Yahoo Fantasy API approval, `YAHOO_CLIENT_ID`, and `YAHOO_CLIENT_SECRET` are not available for verification.
- A real call would hit Yahoo. Do not make one.

The feature stays unverified. The help text is the check that the untested mark is still there. It is not a proof that Yahoo returns leagues.

```bash
python3 league_read.py yahoo --help | grep -q -i untested
```

Exit code 0 means the help text still contains `untested`. `tests.test_league_read.TestYahoo` patches HTTP. A passing run of that class does not mean Yahoo works. Leave it out of the proof.

## Gotchas

- `auth-url` rejects `oob`, localhost, and non-https redirects, and it does not itself perform a token request. Still do not run it with a real client id during verification.
- `connect` writes `yahoo_token.json`. The unittest uses a temporary directory. A hand run would write under the config directory.
- A 403 that says the application is not authorized is `yahoo_not_approved`, and the payload includes `untested`.
- `yahoo get` rejects a full URL and a `..` path.
- Do not put a client id, secret, auth code, or refresh token in the evidence directory.
