# Demo video script (target 2:50)

Record the phone screen (or a browser in phone-sized view) at `<amplify-url>/?demo=1&replay=1`. Speak over it in English; let the app's Hindi voice play.

| Time | On screen | Say (roughly) |
| --- | --- | --- |
| 0:00–0:20 | Street or rider footage in afternoon sun; text: "Only 16.87% of gig workers have shaded rest" | "Delivery riders work through India's hottest hours. They're paid per order, so every break costs them money, and nobody tells them when the heat is too much." |
| 0:20–0:45 | App home: heat map replaying a real May heatwave day, forecast strip | "HeatShield maps heat stress across the city every hour, using WBGT, which accounts for humidity and sun, not just temperature. Here we replay a real heatwave day." |
| 0:45–1:50 | Press **Run demo ride**. Gauge rises. Idle nudge (Hindi voice), on-route nudge with the line to the water point, break logged, gauge drops. Tap **I feel unwell**: first-aid card. | "Each ping adds to the rider's personal heat dose. While they wait for an order, it points to shade nearby: a free break. While riding, it only suggests water that's already on their route. Stop for ten minutes at a water point and the break is logged automatically." |
| 1:50–2:25 | Architecture diagram (README), then the AWS console: EventBridge schedule, `grid` Lambda logs, DynamoDB items, S3 GeoJSON, Polly | "EventBridge rebuilds the heat grid every hour in Lambda from Open-Meteo. Rider pings hit API Gateway and Lambda; state lives in DynamoDB; Polly speaks Hindi and English, cached in S3. The whole stack is one SAM template on the free tier." |
| 2:25–2:50 | **End shift** summary | "Riders get a record of their heat exposure to show unions and platforms when asking for paid heat breaks. Next: heat-aware order allocation for platforms, and construction sites." |

Before uploading: watch it once with the sound on, check that it's under 3:00, and open the YouTube link in a signed-out browser.
