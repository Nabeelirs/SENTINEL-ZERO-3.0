"""Read-only, offline PDF report. Values reuse the unchanged technical export."""
from __future__ import annotations

from html import escape
from io import BytesIO
import json

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..application.data import ExplorerData, magnitude_count, pixel, summary_json
from ..presentation.display import comparison

NAVY = colors.HexColor("#142c43")
TEAL = colors.HexColor("#00766d")
PALE = colors.HexColor("#eef4f5")


def report_record(data: ExplorerData, selected: tuple[int, int] | None = None,
                  threshold_db: float = 6.0) -> dict:
    """Retain JSON schema v1.0 untouched; report-only state is separate."""
    return dict(evidence=json.loads(summary_json(data)),
                selected_cell=pixel(data, *selected) if selected is not None else None,
                exploration=dict(absolute_threshold_db=threshold_db,
                                 inclusive_eligible_count=magnitude_count(data, threshold_db)))


def investigation_pdf(data: ExplorerData, selected: tuple[int, int] | None = None,
                      threshold_db: float = 6.0, *, trust: dict | None = None) -> bytes:
    """Three-page report from actual verified arrays; never changes scientific files."""
    record = report_record(data, selected, threshold_db)
    e, s = record["evidence"], record["evidence"]["statistics"]
    before, after = e["sources"]
    if trust is not None:
        if (trust.get("local_integrity") not in ("FAILED","CONSISTENT_WITH_LOCAL_REFERENCES")
                or trust.get("scientific_quality") != "CONDITIONAL_PROVISIONAL"
                or trust.get("environmental_interpretation") != "UNVERIFIED"
                or trust.get("provider_authenticity") != "UNVERIFIED"
                or bool(trust.get("failed_artifacts")) != (trust["local_integrity"] == "FAILED")):
            raise ValueError("Inconsistent/unsupported evidence trust state")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SZTitle", fontName="Helvetica-Bold", fontSize=26, leading=29, textColor=NAVY, spaceAfter=8))
    styles.add(ParagraphStyle(name="SZHeading", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=NAVY, spaceBefore=14, spaceAfter=8))
    styles.add(ParagraphStyle(name="SZBody", fontName="Helvetica", fontSize=9, leading=13, textColor=NAVY, spaceAfter=7))
    styles.add(ParagraphStyle(name="SZSmall", parent=styles["SZBody"], fontSize=7.5, leading=10, spaceAfter=3))
    styles.add(ParagraphStyle(name="SZLabel", parent=styles["SZSmall"], alignment=TA_CENTER))

    def p(text: str, style: str = "SZBody") -> Paragraph:
        # Provider/user state never enters ReportLab's markup parser unescaped.
        return Paragraph(escape(str(text)), styles[style])

    def heading(text: str) -> Paragraph:
        return p(text, "SZHeading")

    def table(rows: list, widths: list[float] | None = None) -> Table:
        converted = [[p(str(cell), "SZSmall") for cell in row] for row in rows]
        t = Table(converted, colWidths=widths or [166, 333], hAlign="LEFT")
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("ROWBACKGROUNDS", (0, 0), (-1, -1), [PALE, colors.white]),
                               ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        return t

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=48, rightMargin=48,
                            topMargin=42, bottomMargin=42, title="SENTINEL ZERO - Investigation Report",
                            author="SENTINEL ZERO team", subject="Exploratory NISAR HHHH radar-response change", invariant=1)
    story = [p("SENTINEL ZERO 3.0", "SZTitle"), p("EARTH EVIDENCE INTELLIGENCE PLATFORM", "SZSmall"),
             p("Detect the Change. Investigate the Evidence. Protect the Decision."),
             heading("Investigation report"),
             p(f"Sri Lanka exploratory study window near the Kelani River | {before['acquisition_start_utc'][:10]} to {after['acquisition_start_utc'][:10]} UTC"),
             p("PROVISIONAL SCIENCE - exploratory measured radar response; physical causes remain unverified."),
             p("Observation: the radar response differs across this shared window. Inspecting the measurements and their provenance helps separate a measured difference from an unsupported environmental claim.")]
    if trust is not None:
        story.append(p("EVIDENCE PASSPORT: "+trust["local_integrity"]+". Measurements/images below use the verified protected baseline. Active disposable-copy integrity and downstream revalidation are recorded on page 4; no corrupted-copy values are used."))
    panels = comparison(data)
    image_table = Table([[Image(BytesIO(panel.png), width=158, height=158) for panel in panels],
                         [p(label, "SZLabel") for label in (before["acquisition_start_utc"][:10]+" backscatter",
                                                           after["acquisition_start_utc"][:10]+" backscatter", "After minus before (dB)")]],
                        colWidths=[166, 166, 166])
    image_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    story += [image_table, Spacer(1, 7),
              p("North is up. Both dates: shared -25 to +15 dB display scale. Change: purple = lower, teal = greater power, zero = near-white. Display range -6 to +6 dB; grey = excluded. Colour saturation clips display only; no scientific values were clipped.", "SZSmall"),
              p("Display-saturated eligible cells: "+", ".join(f"{label} {panel.clipped_cells:,}" for label, panel in zip(("before", "after", "change"), panels))+".", "SZSmall"),
              table([["Common eligible / excluded", f"{s['eligible_cells']:,} / {s['excluded_cells']:,} cells"],
                     ["Mean / median change", f"{s['mean']:+.6f} / {s['median']:+.6f} dB"],
                     ["Minimum / maximum", f"{s['minimum']:.6f} / {s['maximum']:+.6f} dB"]]),
              heading("What this finding means"),
              p("Positive values mean greater radar backscatter on the after date; negative values mean lower backscatter. Surface moisture, vegetation, geometry, land conditions and residual processing artifacts are possible influences. This is not a flood, damage or land-cover classification."), PageBreak(),
              heading("02 / Scientific record"),
              table([["Collection", before["collection_id"]], ["Before granule", before["granule_id"]], ["After granule", after["granule_id"]],
                     ["Before UTC", before["acquisition_start_utc"]+" to "+before["acquisition_end_utc"]],
                     ["After UTC", after["acquisition_start_utc"]+" to "+after["acquisition_end_utc"]],
                     ["Product", "NISAR L2 PROVISIONAL GCOV / Frequency A / HHHH gamma-zero power"],
                     ["Processing", f"CRID {before['processing_crid']} / version {before['product_version']} (recorded provider metadata)"],
                     ["Grid", f"EPSG:{e['grid']['epsg']} / {e['grid']['width']} x {e['grid']['height']} / {e['grid']['resolution'][0]:g} m posting"],
                     ["Footprint", "Approximately 5.53 km along each projected grid dimension; not the full Kelani Basin."]]),
              heading("Method and numerical summary"),
              p("Delta_dB = 10 * (log10(P_after) - log10(P_before)). Calculated in SZ-006 from common eligible, positive finite linear power; the app reads the saved float32 change raster. No reprojection, resampling, filtering or scientific recalculation is performed by this report."),
              table([["Standard deviation", f"{s['standard_deviation']:.6f} dB (population)"],
                     ["Positive / negative / exactly zero", f"{s['positive_cells']:,} / {s['negative_cells']:,} / {s['exactly_zero_cells']:,} cells"],
                     ["Current descriptive magnitude", f"|change| >= {threshold_db:g} dB: {record['exploration']['inclusive_eligible_count']:,} eligible cells. Not a validated detection threshold."]]),
              heading("Selected cell (optional)")]
    cell = record["selected_cell"]
    if cell is None:
        story.append(p("No inspected cell was included. Use Investigate Science to select a native row and column."))
    else:
        def value(key: str) -> str:
            v = cell[key]
            return "Excluded / unavailable" if v is None else repr(v)
        story += [table([["Native row / column", f"{cell['row']} / {cell['column']}"],
                         ["Eligibility", "Common eligible" if cell["eligible"] else "Excluded: "+cell["primary_exclusion_reason"]],
                         ["UTM pixel centre", f"E {cell['easting_m']:.1f}, N {cell['northing_m']:.1f} m"],
                         ["Before / after power", value("before_linear_power")+" / "+value("after_linear_power")],
                         ["Saved change", value("saved_change_db")+" dB"]])]
    story += [PageBreak(), heading("03 / Verification and limitations"),
              p("Integrity: source subsets, preparation manifest and analysis artifacts passed the local receipt checks before loading. Computation: the existing loader verifies saved results. Scientific interpretation: CONDITIONAL; independent physical causes and calibration are not established."),
              p("Matching SHA-256 checksums establish consistency with recorded references. They do not prove NASA authenticity, independent calibration or scientific correctness."),
              table([["Preparation manifest SHA-256", e["preparation_manifest_sha256"]],
                     ["Analysis manifest SHA-256", e["analysis_manifest_sha256"]],
                     ["Saved change SHA-256", e["artifact_receipts"]["change"]["sha256"]]]),
              heading("Scientific limitations")]
    for limitation in e["limitations"]:
        story.append(p("- "+limitation, "SZSmall"))
    story += [p("Unresolved quality flags: "+"; ".join(k.replace("_", " ")+" = "+v for k, v in e["quality_flags"].items()), "SZSmall"),
              heading("Evidence path and next investigation"),
              p("NASA NISAR sources > official Harmony subsets > SZ-005 common-support preparation > SZ-006 saved dB analysis > this investigation report. Original HDF5 metadata and subset numerical fidelity are incompletely verified."),
              p("Next scientific steps: inspect unresolved provider QA and possible artifacts, validate physical co-registration, then seek temporally and spatially suitable independent observations before attributing an environmental cause. "+
                ("Physics Witness, extended EarthBOM/Digital Twin and controlled restoration remain planned." if trust is None else
                 "Current EarthBOM, local dependency traversal and disposable-copy recovery are recorded separately on page 4. Independent environmental evidence and stronger scientific validation remain subject to source availability and suitability.")),
              heading("Source attribution"),
              p("NASA-ISRO NISAR data; NASA Earthdata/ASF discovery and Harmony-derived subsets. Independent team application; no NASA endorsement implied.", "SZSmall"),
              p("ASF GCOV guide: https://nisar-docs.asf.alaska.edu/gcov/", "SZSmall"),
              p("Provisional issues: https://nisar-docs.asf.alaska.edu/provisional-known-issues/", "SZSmall"),
              p("Evidence verified UTC: "+e["verified_at_utc"]+" | "+e["mode"], "SZSmall")]

    if trust is not None:
        demo = trust.get("demonstration",{})
        witness = trust.get("witness",{})
        actions = [event for event in demo.get("events",[]) if event.get("action") != "VERIFIED"]
        last = actions[-1]["action"] if actions else "NO_CONTROLLED_CHALLENGE"
        story += [PageBreak(),heading("04 / Evidence Trust Passport"),
                  p("This snapshot separates local byte consistency, scientific quality and environmental interpretation. It is not a probability of correctness."),
                  table([["Local integrity / demo state",trust["local_integrity"]+" / "+demo.get("state","NOT_STARTED")],
                         ["Scientific quality",trust["scientific_quality"]],
                         ["Provider authenticity",trust["provider_authenticity"]],
                         ["Environmental cause",trust["environmental_interpretation"]],
                         ["Controlled sequence",last],
                         ["Failed disposable artifact",", ".join(trust.get("failed_artifacts",[])) or "None"],
                         ["Dependencies needing revalidation",", ".join(trust.get("requires_revalidation",[])) or "None flagged by local byte checks"]]),
                  heading("Integrity and recovery scope"),
                  p("The controlled challenge modifies only a service-created disposable statistics copy. Original NISAR subsets, saved scientific products, manifests and fallback release remain untouched. Recovery copies verified original bytes and rechecks the same independent local SHA-256 reference. It never changes an acceptance hash to make a failed artifact pass."),
                  p("All measured values in this PDF come from the protected baseline, not the challenged copy. A failed disposable dependency requires revalidation in the demonstrated pipeline even when the baseline finding itself remains reproducible. Local journal/history is not an external trust anchor; a compromised host can undermine local checks."),
                  heading("Physics Witness / recorded environmental context"),
                  p(witness.get("status","INSUFFICIENT_PHYSICAL_EVIDENCE")),
                  p(witness.get("reason",witness.get("interpretation","No suitable recorded independent environmental source was verified."))),
                  p("Environmental context, if available, is independent point/interval evidence, not confirmation that rainfall caused radar change. Product radiometry, RFI/banding, original QA, subset fidelity and physical co-registration remain unresolved after local recovery."),
                  heading("Evidence path"),
                  p("Recorded NASA scenes > Harmony source subsets > eligibility outputs and manifest > saved change/statistics/diagnostics > analysis manifest > evidence passport. Dependency edges trace computation and provenance, not environmental consequences."),
                  p("OpenAI Codex assisted code, tests and documentation; the NISAR measurements are actual recorded observations, not AI-generated satellite data.","SZSmall")]

    if trust is not None and (trust.get('rebuild') or trust.get('decision_impact')):
        rebuild=trust.get('rebuild') or {}
        impact=trust.get('decision_impact') or {}
        witness=trust.get('witness') or {}
        story += [PageBreak(),heading('05 / Reconstruction and decision implications'),
                  p('This page distinguishes verified numerical reconstruction from byte restoration, and recorded dependency failures from hypothetical human decisions.')]
        if rebuild:
            story += [heading('Recomputed derived evidence'),
                      table([['Operation',rebuild.get('operation','UNKNOWN')],
                             ['Numerical equivalence',str(rebuild.get('numerically_equivalent','UNVERIFIED'))],
                             ['Byte-identical to trusted reference',str(rebuild.get('byte_identical','UNVERIFIED'))],
                             ['Current revalidation result',rebuild.get('local_revalidation','UNVERIFIED')],
                             ['Expected SHA-256',rebuild.get('expected_sha256','UNVERIFIED')],
                             ['Regenerated SHA-256',rebuild.get('regenerated_sha256','UNVERIFIED')]]),
                      p(rebuild.get('method','Method unavailable'), 'SZSmall'),
                      p('Reconstruction recomputes statistics from the reverified saved change raster and common support. It does not copy the baseline statistics bytes or change the acceptance hash. Provisional science and unverified physical causes remain.', 'SZSmall')]
        else:
            story.append(p('Recomputation has not been performed in this session. A byte-restoration event must not be described as scientific reconstruction.'))
        if impact:
            story += [heading('Decision Impact Analyzer'),
                      table([['Selected evidence',impact.get('selected_artifact','UNKNOWN')],
                             ['Direct dependent artifacts',', '.join(impact.get('direct_dependents',[])) or 'None'],
                             ['Flagged evidence in this path',', '.join(impact.get('currently_flagged',[])) or 'None'],
                             ['Dependent reports',', '.join(impact.get('dependent_reports',[])) or 'None']]),
                      p(impact.get('recorded_consequence','Unverified dependency state')),
                      p('Hypothetical guidance: '+impact.get('hypothetical_decision_consequence','No decision implication assessed')),
                      p('Dependency edges record computation/provenance. These implications are not observed disaster consequences, losses or operational alerts.', 'SZSmall')]
        if witness.get('rows'):
            m=witness.get('metadata',{})
            story += [heading('Recorded environmental context'),
                      table([['Provider / variable',str(witness.get('provider',m.get('provider','UNKNOWN')))+' / '+str(m.get('variable','UNVERIFIED'))],
                             ['Original response/export SHA-256',m.get('sha256','UNVERIFIED')],
                             ['Observation location / cadence',str(m.get('latitude'))+', '+str(m.get('longitude'))+' / '+str(m.get('interval_hours'))+' h'],
                             ['Units / missing records',str(m.get('units','UNVERIFIED'))+' / '+str(witness.get('missing_values','UNVERIFIED'))]]),
                      p('Source: '+str(m.get('source_url','UNVERIFIED')), 'SZSmall'),
                      p(witness.get('accumulation_rule','No accumulated totals assessed'), 'SZSmall')]
            for period in witness.get('accumulation_periods',[])[:4]:
                story.append(p(str(period.get('start_utc'))+' to '+str(period.get('end_utc'))+': '+str(period.get('total_mm'))+' mm over '+str(period.get('sample_count'))+' complete recorded intervals.', 'SZSmall'))
            if len(witness.get('accumulation_periods',[]))>4:
                story.append(p('Additional discontinuous accumulation periods remain in the technical environmental export.', 'SZSmall'))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(TEAL)
        canvas.line(48, 32, A4[0]-48, 32)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(48, 20, "SENTINEL ZERO / Exploratory NISAR evidence / PROVISIONAL")
        canvas.drawRightString(A4[0]-48, 20, str(document.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
