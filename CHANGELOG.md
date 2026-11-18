# Changelog

## 0.4.0

- On the touchscreen, printer alerts now appear as a count badge on the Status button instead of a banner. The badge is coloured by severity and pulses for errors.

## 0.3.1

- The updater now flushes files to disk before restarting, reducing the risk of empty files after a power loss.

## 0.3.0

- Printer alerts now show only items the printer itself displays (error, warning, notification) and are coloured by severity.
- Alerts show descriptions from the Bambu HMS wiki for each configured printer model, with a setting to show the raw codes.
- Added a local description for the E1 purifier high PM2.5 alert.

## 0.2.0

- Added printer status details, alerts, and reported USB storage capacity to both views.
- Labelled AMS slots by unit, such as A1-A4 and B1-B4.
- Preserved partial updates for nozzle, fan, and light telemetry.

## 0.1.1

- Improved the touchscreen filament layout and readability.
- Added example remaining filament percentages to demo mode.

## 0.1.0

- Added desktop and touchscreen dashboards for Bambu Cloud printer status.
- Added print previews, temperatures, air purifier readings, and AMS filament status.
- Added light and dark themes and a three-printer demo mode.
- Added optional remembered sessions and Raspberry Pi kiosk installation.
- Added version information and in-app checks for stable GitHub releases.
