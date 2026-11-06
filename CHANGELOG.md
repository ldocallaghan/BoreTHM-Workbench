# Changelog

## 0.3.0.dev0

Scenarios select and record current or previous model physics. Schema 1.1 records the choice; schema 1.0 loads with previous physics and an explanation. Reproduction and checkpoints require matching recorded physics.

The high-temperature preset completes under previous physics. Current physics exceeds the water-property table's temperature limit for this preset, so the application rejects the run with `PROPERTY_DOMAIN_OUTSIDE`.

Joint editing supports kind-dependent defaults and tapered transitions with an included angle. Segments can select named tool classes and collar sizes, with generic allowances displayed beside supplied figures. A supplied loss overrides its generic allowance.

First version in this repository. Installs BoreTHM from PyPI. Requires BoreTHM 1.8.0 or later within the 1.x release line. History modes and event checkpoints are available with the installed package. Earlier example archives and checkpoints, including those from Workbench 0.2.0rc2, are unsupported.
