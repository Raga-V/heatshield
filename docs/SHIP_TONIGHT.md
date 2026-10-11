# Ship tonight: run-of-show

Find the exact submission cut-off on Discord or in your email **first**, then work backwards. Times below assume about 5 hours. If you have less, skip the steps marked *optional*.

| Block | Who | Do | Done when |
| --- | --- | --- | --- |
| 0:00–0:15 | All | Create the public GitHub repo, add this code, commit as yourselves, push. Read `core.py` together so everyone can explain it in an interview. | Repo public, first commit today |
| 0:00–0:45 | A | Install AWS CLI v2 + SAM CLI, `aws configure`, set a $10 budget alert. Run `./scripts/deploy.sh` (Git Bash on Windows). It also publishes the app to Amplify. | `curl <ApiUrl>/config` returns JSON; script prints the Amplify URL |
| 0:15–0:45 | B | `python3 backend/tests/test_heatshield.py` and `python3 scripts/local_server.py`; click through the app at `/?demo=1&replay=1` and note any UI bugs | All 9 tests pass, demo ride runs locally |
| 0:45–1:00 | A | Run the `scan` mode, pick the hottest real day, redeploy with that `REPLAY_DATE` | Replay map shows red cells |
| 1:00–1:20 | C | Open the Amplify URL the deploy script printed on a real phone. (Manual fallback: Amplify console → Deploy without Git → drag `web.zip`.) | App loads on the phone over HTTPS |
| 1:20–1:40 | C, D | On the phone: start a real shift for 3 minutes (gauge moves), add one water point, run the demo ride | Everything works on the deployed URL |
| 1:40–3:00 | D (+B) | Record the video (see `DEMO_VIDEO.md`). Screen-record the phone; record AWS console clips. | Under 3:00, uploaded to YouTube as unlisted |
| 3:00–3:30 | D | Paste `WRITEUP.md` into the form; publish it as a Builder Center blog post too (*optional*, AirPods) | Writeup final |
| 3:30–4:00 | All | Checklist below, then **submit** | Confirmation email received |

## If something breaks

- **`sam deploy` fails on ScheduleV2:** update SAM CLI; or change the event to `Type: Schedule` with `Schedule: cron(5 * * * ? *)`.
- **Polly neural voice error:** the code falls back to the standard voice automatically; check the Lambda logs in CloudWatch.
- **Overpass times out:** invoke the seed Lambda again; or seed hand-checked points with `{"extra":[{"lat":..,"lon":..,"kind":"water","name":".."}]}`.
- **Map tiles blank:** OpenStreetMap tiles need internet; or add an Amazon Location API key to `web/config.js` (also adds AWS depth).
- **No time to deploy at all:** record the UI from the local server *and* still show AWS: deploy at least the stack and show Lambda + DynamoDB in the console. The rules require the video to show AWS in use.

## Submission checklist

- [ ] Public repo with README; AI tools listed
- [ ] Commits dated within the event
- [ ] YouTube video under 3:00, unlisted or public, opens in a signed-out browser
- [ ] Video shows AWS running (console), not just named
- [ ] Writeup: problem, build, where AWS fits
- [ ] Every member's registration and Builder Center student verification correct
- [ ] Submitted before the deadline
