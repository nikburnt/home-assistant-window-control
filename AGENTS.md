# Window Control

Source code is owned by this repository, not the Home Assistant `/config`
snapshot. Publish tested releases and install them through HACS. Never patch
the installed integration to bypass publication.

Keep the per-member latest-intent semantics documented in README.md. Commands
must not be restored on startup, reload, or source recovery. Test changes with
simulated covers; do not move real covers unless the owner explicitly requests
a hardware test. Preserve service contexts and isolate transport failures.

Run `pytest -q` and `ruff check custom_components tests` before release. New
entities require an explicit storage audit in the HA configuration repository;
do not automatically add them to InfluxDB.
