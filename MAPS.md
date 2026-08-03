# Map sources & update references

Official references for MIB2 map updates (as collected on SEATCUPRA.NET):

- https://www.seatcupra.net/forums/threads/updating-the-inbuilt-mib2-satnav-mib2-tricks-and-mib1.388586/
- https://mib-helper.com/show.php?all=maps

## Version info

The map version number shown on the unit is the version of the *installed map*
(e.g. `0635`, `2510`, `2610`, `2710`), not the unit's firmware. The firmware
"Softwaretrain" (e.g. `MST2_EU_SE_ZR_P0405T`) can be read in the car under
`SETUP/MENU -> Version info`; it does **not** need to change for a map update
(maps-only update, see `ANALYSE.md`).

Optional cosmetic step (documented, usually **not** applied): editing a
`PartNumberX` line in `maps/00/nds/dbinfo.txt` on the card to the car's part
number only changes the version screen / dealer diagnostics. It has no effect
on whether the maps load (acceptance is the `OVERALL.NDS` signature + SD CID,
not dbinfo.txt), and it makes the card drift from the package checksums.

## Direct VW download URLs

DiscoverMedia2 / MIB2 Standard stream, ECE region. The official VW server only
hosts the newest release; the URL pattern is
`https://navigation-maps.volkswagen.com/vw-maps/Update_<YY>_<YY>/DiscoverMedia2_<region>_<version>_V<build>.7z`

Known 2710 (ECE 2027, V24) packages:

```
https://navigation-maps.volkswagen.com/vw-maps/Update_25_26/DiscoverMedia2_EU1_2710_V24.7z
https://navigation-maps.volkswagen.com/vw-maps/Update_25_26/DiscoverMedia2_EU2_2710_V24.7z
https://navigation-maps.volkswagen.com/vw-maps/Update_25_26/DiscoverMedia2_EU3_2710_V24.7z
https://navigation-maps.volkswagen.com/vw-maps/Update_25_26/DiscoverMedia2_EU-DL1_2710_V24.7z
https://navigation-maps.volkswagen.com/vw-maps/Update_25_26/DiscoverMedia2_EU-DL2_2710_V24.7z
https://navigation-maps.volkswagen.com/vw-maps/Update_25_26/DiscoverMedia2_EU-DL3_2710_V24.7z
https://navigation-maps.volkswagen.com/vw-maps/Update_25_26/DiscoverMedia2_EU-DL4_2710_V24.7z
https://navigation-maps.volkswagen.com/vw-maps/Update_25_26/DiscoverMedia2_EU-AS_2710_V24.7z
```

`updates.json` in this repo lists the known releases with sizes and country
notes; the UI can probe these URLs (online + size) and walk the URL pattern
to discover newer releases automatically.
