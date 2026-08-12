# Eurasia pack — Russia, Europe and the stepland

Region three. The continental system: the Washington–Moscow dyad, the European
core that sits between them, the Baltic and Visegrád belt that changed sides,
the Caucasus, and the Central Asian steppe that is still choosing.

## What is different about this lens

**It declares its own externality.** `actors.yaml` sets `external_powers: []`.
The GDELT loader's default drops USA–RUS wire traffic as an external-power
pair, which is correct for the Gulf and the Strait — there it is flood noise
about somewhere else. Here it is the spine, and a Eurasia archive without the
Washington–Moscow dyad would be an archive of the Cold War with the Cold War
taken out. Making this a pack-declared value (rather than a region name in the
loader) is the contract holding: the core learned a general capability, not a
special case.

**Its sovereign money is visible.** This is the first pack whose SWF actually
files 13F — Norges Bank, CIK 1374170, the largest sovereign equity portfolio
in the world. MENA records ADIA's absence and China records CIC's and SAFE's;
here the FLOW edges are real.

**It has a hole shaped like sanctions.** MOEX is in `markets.yaml` and is
expected to produce `skipped_no_data` for recent events: Yahoo's coverage of
Russian domestic indices became unreliable at exactly the moment Russian risk
materialised. The market is real, the ticker is the fixable part, and the
depth report is what surfaces it — the same posture as the MENA pack's Abu
Dhabi ticker.

## Deliberate omissions, so absence is not read as evidence

- **Pre-2015 armed disputes are not in the spine.** Berlin 1948, Hungary 1956,
  Prague 1968, Afghanistan 1979 and Georgia 2008 arrive through the COW deep
  tier; coding them here as well would fold those dyads' baselines twice. The
  curated rows are the treaties, the partitions and the sanctions.
- **Nord Stream's 2022 sabotage is not an event.** Attribution is contested
  and the archive does not code an initiator it cannot name. A curated row
  asserting one would be exactly the failure the provenance rule exists to
  prevent.
- **The European gas price is a proxy.** `NG=F` (Henry Hub) stands in for TTF,
  whose free-feed history is too thin to measure against, and `markets.yaml`
  says so at the market rather than in a footnote.
- **The two German states carry no `iso3`.** GDELT's country vocabulary is
  modern — DEU means Germany at every date — so giving the GDR an ISO code
  would route 1980s wire traffic to a state the coder never meant. Their COW
  windows are what make them queryable at the dates they existed.

## Core changes that came with this pack

Two, both generalizations rather than region special-cases:

- `external_powers` became a pack property (`core/packs.py`) threaded into
  `gdelt.parse_lines`, with the previous hardcoded `{USA, RUS}` preserved as
  the default so MENA and China are unaffected.
- CAMEO `081` was added to `cameo_goldstein.yaml`, which the file's own header
  invites ("add a code here when a loader starts citing it") — the spine cites
  it for the opening of the Berlin crossing points.
