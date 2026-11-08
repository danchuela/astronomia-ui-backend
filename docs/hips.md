# HiPS delivery

The viewer uses `/hips/metadata?survey_id=...` to locate one of the products in
`app/hips.py`. This fixed catalog is independent of the live CDS registry.
`creator_did`, release date, frame, order, tile width and formats are validated.
The identity determines a revision in all subsequent resource URLs. A mirror
with a different release or geometry cannot supply tiles for that revision.

The proxy accepts only configured product roots and canonical HiPS image tile
paths, properties and the optional coverage map. It never accepts arbitrary
URLs, follows redirects, forwards credentials or manufactures images. Full
properties are retained because Aladin needs the product type and rendering
metadata, not only a title and URL. All image bytes are forwarded unchanged.

Downloads have a total deadline, eight network slots, a bounded queue, an 8 MiB
file limit and a 32 MiB / 256-entry memory cache. Connections are reused and
closed on shutdown. Local backpressure and individual file read failures do not
mark the complete origin unavailable. Coverage maps are optional in HiPS: if
one cannot be fetched within its short budget, the proxy exposes no map (404),
without generating replacement coverage or relaxing image-tile checks.

Resource timing and HEAD are available for the frontend's load checks. HEAD
returns the validated cached file length without a body. Public image responses
use `Timing-Allow-Origin` and `nosniff`; application CORS policy still applies.

## Sources

- [IRSA HiPS list](https://irsa.ipac.caltech.edu/data/hips/list): SDSS9, DSS2,
  2MASS and AllWISE replicas.
- [MAST HiPS list](https://stpubdata.s3.us-east-1.amazonaws.com/mast/skybackgrounds/hips_list):
  DSS2 and PanSTARRS replicas.
- [CDS HiPS registry](https://aladin.cds.unistra.fr/hips/): product identities
  and other registered provider roots.

The provider catalog is not a promise that every origin is online. Products
without a reachable matching replica remain unavailable; another survey, band,
release or resolution is never silently substituted. This service fetches small
requested resources and uses a bounded HTTP cache; it does not clone HiPS sets.
