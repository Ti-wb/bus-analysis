<picture>
  <source media="(prefers-color-scheme: dark)" srcset="branding/assets/png/logo-horizontal-reverse@2x.png">
  <img src="branding/assets/png/logo-horizontal-tagline@2x.png" alt="Bus coming soon — Open data bus network GIS platform" width="640">
</picture>

# Bus coming soon | Bus Network & Demand Analysis

[繁體中文](README.md) · **English**

An open-data map for exploring bus services and comparing route changes. It helps public agencies, bus operators, drivers, and residents understand current services, identify questions worth investigating, and try out route alternatives.

**Explore services → Prioritize improvements → Edit a route scenario → Compare impacts → Discuss together**

[Open the app](https://bus.tw.pe/)

## What you can do

| Feature | How to use it |
| --- | --- |
| Explore the map | Switch between stop coverage, scheduled bus frequency, demand–supply gaps, and neighborhood age profiles. Compare frequency and demand by weekday/weekend and time of day. |
| Inspect stops and their surroundings | Search for bus, metro, and YouBike stations to view nearby population, age profiles, and facilities. Bus stops also offer a focused view of their service routes. |
| Prioritize improvements | Filter and rank routes, stops/segments, candidate areas for demand-responsive transport services (DRTS), or terminal stops. Switch between list and map views, inspect scores and details, and open a related route in the editor. |
| Try route changes | Add, edit, or remove routes; adjust stop order, alignment, and service frequency for a selected time period. Consult nearby services when adding a route. The map focuses on the current task as you select stops, adjust the path, and preview changes. |
| Compare scenario impacts | View service gains/losses separately from scenario frequency, and compare population coverage, nearby facilities, and estimated travel time along the draft route. Preview changes, apply them, review their impacts, or undo them. |
| Share and discuss | Share the view settings for the exploration map or improvement list, export filtered results as CSV, and check data sources and limitations. |

## Where to start

Follow the quick tour on your first visit. You can reopen it from **資料與方法** (Data & Methods). Start with a familiar stop, explore its services, then choose a candidate from the improvement list and try changes in the route editor.

The app interface and detailed guides are currently in Traditional Chinese.

## What to keep in mind

- **Coverage varies by feature.** Frequency and some population/age analyses cover Taipei, New Taipei, and Keelung. Stop coverage, demand–supply gaps, improvement priorities, and route scenarios remain primarily based on Taipei data.
- **Schedules and estimates are not observed outcomes.** Frequency uses published schedules, coverage uses straight-line distances, and population/age figures include area-based estimates. These do not represent real-time arrivals, actual walking access, or ridership.
- **Improvement priorities include mock data.** Some demand indicators, scores, and recommendations use illustrative data. They support hypotheses to investigate, not direct decisions to cut service or remove routes.
- **Route scenarios stay in the current browser.** Applied changes can be retained and undone; unapplied drafts are lost on reload. Shared links do not include local scenarios, and edits do not change real-world services.
- **Some assessments are not yet available.** Scenario impacts on older adults and students, as well as full operational and driver-rest feasibility assessments, are not implemented. Draft alignments have not been validated as drivable routes.

## Learn more

- [Map guide](docs/web-map.md) · [Improvement list](docs/improvement-list.md) · [Route editor](docs/route-editor.md)
- [User stories and design references](docs/product-stories.md) · [Documentation and data sources](docs/README.md) · [Brand assets](branding/)

## License

This project is licensed under the [MIT License](LICENSE). External datasets, map data, and third-party packages remain subject to their respective licenses.
