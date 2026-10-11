# HeatShield: submission writeup

## The problem

India lost an estimated 247 billion potential labour hours to extreme heat in 2024 (Lancet Countdown 2025), and construction and outdoor work carry much of it. App-based delivery riders are among the most exposed: they ride through the afternoon, are paid per order, and have no supervisor. In a Telangana union survey, only 16.87% of gig workers had access to shaded rest areas. In April 2026 the Labour Ministry told employers to reschedule work and provide water and rest, but a rider has no employer on site to act on that advisory.

The barrier is not awareness alone. **A break costs a rider money.** A tool that tells riders to stop will be ignored.

## What we built

A phone web app that runs during a shift and stays quiet until a break is nearly free:

1. **Live heat map.** A WBGT grid of the city (about 2 km cells), rebuilt every hour, coloured green, yellow or red.
2. **Personal heat dose.** Every GPS ping adds minutes weighted by the heat band of the rider's current cell.
3. **Spoken nudges in Hindi or English**, designed around income:
   - *Idle:* waiting for an order? Shade and water within 300 m.
   - *On route:* a water point within 500 m in your direction of travel. No detour, so no lost income.
   - *Critical:* only at the dose limit, or when the rider taps "I feel unwell", with first-aid steps.
4. **Automatic break detection.** Ten minutes stopped at a water or rest point counts as a break; the dose recovers.
5. **Shift summary:** minutes in red zones, breaks taken, the hottest WBGT, the route. This gives riders evidence to show a union or platform when asking for paid heat breaks.
6. **Crowdsourced water points:** a rider long-presses to add one, and a second rider confirms it.

## Where AWS fits

| Service | Role |
| --- | --- |
| EventBridge Scheduler | Runs the heat-grid build every hour |
| Lambda (3 functions) | Heat grid and replay; rider API (dose, breaks, nudges); OpenStreetMap seeding |
| DynamoDB | Heat cells by hour, shifts, pings, water points (GSI by grid cell for nearby lookup) |
| S3 | Hourly GeoJSON heat layers; Polly audio cache |
| Amazon Polly | Hindi and English voice nudges, generated once and cached |
| API Gateway (HTTP API) | The app's single backend |
| Amplify Hosting | Serves the app over HTTPS, which browsers require for location |
| AWS SAM (open source) | The whole stack as code, deployed with one command |

The stack runs within the free tier.

## Engineering choices

- **Replay mode.** Our October demo replays a real May heatwave day from Open-Meteo's archive, so judges see the system under real conditions rather than mild October weather.
- **Cheap per ping.** A ping becomes a grid lookup by arithmetic (no geo database), one point query and two writes.
- **Rules in config, logic in pure functions.** Thresholds sit in `config.json`; dose and nudge rules have unit tests plus an end-to-end test that replays a scripted ride and checks the nudge order.
- **Cached audio, so no data is used per nudge.** Riders report that phones overheat and shut down, so we keep the app light.

## Limitations and next steps

WBGT is estimated from forecast data (shade approximation plus a direct-sun allowance); thresholds are planning assumptions for an occupational-health expert to set; the web app needs the screen on, so production would be a native app. Next: heat-aware order allocation for platforms (shorter orders past rest points when a rider's dose is high, with no loss of orders), and the same engine for construction sites.

## AI tools used

Claude (Anthropic) for research, planning, code, tests and documentation, reviewed and run by the team.
