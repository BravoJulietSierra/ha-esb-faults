# ESB Networks PowerCheck Faults integration for Home Assistant

This repository contains a Home Assistant integration for  [ESB Networks PowerCheck](https://powercheck.esbnetworks.ie/) display notable service interruptions for a given location, within a configured proximity. The integration relies upon a connection to ESB Network's PowerCheck API.

##  Installation

You will need [HACS](https://hacs.xyz) installed in your Home Assistant server. Install the integration by installing this repository as a Custom Respository (working on adding this to the default HACS integration set). Then, navigate to Integrations, Add an Integration and select ESB Faults. You will then be asked to enter:

* Latitude: Your location latitude
* Longitude: Your location longitude
* Proximity: Filters alerts to within `X` kilometers of your lat/lon.
* API Key: I won't include this here, but you can find it by inspecting requests on the PowerCheck website. Look for the `Api-Subscription-Key` key.

[![Open your Home Assistant instance and add this integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=esb_faults)


## Entities

The integration reads data 5 minutes. It exposes one entity, `sensor.esb_faults_sensor`. The *State* of the sensor is a numeric value, indiciating how many total faults (be they Planned Outages, Recent Restorations or Current Unplanned Faults) are present nearby. The attributes for the sensor include a an `outages` array, which contains details of individual outages present. Fields for these individual outages include:

| Outage Field    | Description                                                  |
|-----------------|--------------------------------------------------------------|
| outageType      | Outage type (e.g. 'Planned', 'Fault', 'Restored')            |
| location        | Outage location (area name)                                  |
| latitude        | Latitude of the outage pin on the PowerCheck map             |
| longitude       | Longitude of the outage pin on the PowerCheck map            |
| distance        | Distance in km from your configured lat/lon                  |
| nearest_road    | Nearest named road to the outage pin (via OpenStreetMap)     |
| location_detail | Area and road combined, e.g. `Sutton (near Moyclare Rd)`     |
| plannerGroup    | Region                                                       |
| numCustAffected | Number of customers affected                                 |
| startTime       | Outage start time                                            |
| estRestoreTime  | Estimated restoration time                                   |
| statusMessage   | Status Message                                               |
| restoreTime     | Restoration time (only for outages of outageType `Restored`) |
| numCustAffected | Number of customers affected                                 |


### Nearest road lookup

The nearest road is found by reverse geocoding the outage coordinates with [OpenStreetMap Nominatim](https://nominatim.org/). Each outage is looked up once and cached until it disappears from the PowerCheck feed, so the integration stays well within Nominatim's usage policy. If the lookup fails, `nearest_road` is `null` and `location_detail` is omitted. Road accuracy depends on OpenStreetMap data and the pin may sit at a network asset rather than the exact fault.

## Sample Automation to notify about nearby outages

```yaml
alias: ESB Outage Notifications
description: ""
trigger:
  - platform: state
    entity_id:
      - sensor.esb_faults_sensor
    attribute: outages
condition: []
action:
  - service: notify.notify
    data:
      title: ⚡ ESB Outage Notification ⚡
      message: >-
        Type: {{state_attr('sensor.esb_faults_sensor', 'outages')[0].type}},
        Location: {{state_attr('sensor.esb_faults_sensor',
        'outages')[0].location_detail
        | default(state_attr('sensor.esb_faults_sensor',
        'outages')[0].location)}}, Distance:
        {{state_attr('sensor.esb_faults_sensor', 'outages')[0].distance}} KM,
        Estimated Restore: {{state_attr('sensor.esb_faults_sensor',
        'outages')[0].estRestoreTime}}
mode: single
```
