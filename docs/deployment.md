# Deployment preparation

**No public application has been deployed or verified.** No public app URL is
invented here. The default offline demonstration does not need provider credentials.

Recommended first option: **Streamlit Community Cloud**, with repository
`Nabeelirs/SENTINEL-ZERO-3.0`, reviewed branch, Python **3.11**, entry
`public_app.py`, root `requirements.txt`. The entry opens the accepted Earth
Atlas; the older `tools/hosted_app.py` opens the legacy Explorer and is not the
chosen judge-facing entry. Source code is unchanged between local and hosted mode.

Official references checked during this release audit:

- [File organization](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization)
- [Dependencies](https://docs.streamlit.io/deploy/concepts/dependencies)
- [Deploy](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)
- [Resource limits and sleep behavior](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)

Those resource allocations may change. Do not treat historical documented quotas
or local measurements as guaranteed cloud capacity. Python pins, native Rasterio
wheels and new Streamlit component assets need actual Linux-host validation.

## Required operator decisions and checks

1. Clear source/contributor licensing, NASA/ISRO data and derived-image reuse,
   and retained third-party notices. Approve the exact publication manifest.
2. Provision the unchanged verified 14.93 MB archive at the approved location.
   The source-only overlay omits it. No automatic remote data download is added.
3. Give scientific inputs read-only storage where supported, with a separate
   writable temporary cache/lab parent. Visitors cannot choose filesystem roots.
4. Test anonymous concurrent sessions, report generation, upload bounds, explicit
   CMR searches and cache invalidation under actual CPU/memory limits.
5. Establish retention/admission handling for disconnected disposable labs. The
   current 100-directory shared-parent cap is deliberate but is not automatic
   expiry. A long-running public host needs tested lifecycle management; do not
   weaken path/reference guards or delete protected inputs to regain capacity.
6. Keep CORS/XSRF defaults enabled. Use platform HTTPS; do not expose a local
   development server or credentials. A public visitor must not gain operator CLI,
   arbitrary path, source overwrite or baseline-replacement access.
7. Test a real cold start and wake-from-sleep, missing/corrupt data error, all
   five stages, two independent session copies, rebuilding, PDF and mobile layout.
8. Open the final HTTPS URL from an independent unauthenticated client. Record
   actual success before adding it to README/NASA materials.

The native pilot is only 305,809 cells, but verification, PNG payloads, report
caches and simultaneous sessions still cost memory/CPU. The audit records local
measurements separately; no throughput promise is made. Community Cloud may
hibernate inactive applications, so rehearse before judging.

If the supported host cannot be accepted in time, use the verified private local
demo plus a genuine recorded short demonstration. Label that as a recording,
not a live public application. Publication, deployment and NASA submission are
three separate owner actions.
