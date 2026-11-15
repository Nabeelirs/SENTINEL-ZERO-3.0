"""Reusable evidence-first presentation primitives; no scientific processing."""
from __future__ import annotations

from html import escape
from ..application.data import ExplorerData, pixel

GUIDE_QUESTIONS = ("What changed?", "Why is this conditional?", "Explain my selected cell", "What does integrity recovery prove?")


def section_heading(number: str, eyebrow: str, title: str, description: str = "") -> str:
    """Escape every dynamic label before it enters the shared component markup."""
    return ('<div class="uix-section-heading"><span class="uix-section-number">' + escape(number) +
            '</span><div><div class="uix-eyebrow">' + escape(eyebrow) + '</div><h2>' + escape(title) +
            '</h2>' + ('<p>' + escape(description) + '</p>' if description else '') + '</div></div>')


def guide_answer(data: ExplorerData, question: str, selected: tuple[int, int] | None, trust: dict | None = None) -> str:
    """Deterministic recorded-evidence explanations, explicitly not an AI service."""
    if question not in GUIDE_QUESTIONS:
        raise ValueError("Unsupported evidence guide question")
    stats = data.product.statistics
    if question == GUIDE_QUESTIONS[0]:
        sources = data.product.provenance['sources']
        return (f"Between {sources[0]['acquisition_start_utc'][:10]} and {sources[1]['acquisition_start_utc'][:10]}, "
                f"the saved HHHH radar-response change has a median of {stats['median']:+.6f} dB "
                f"across {stats['eligible_cells']:,} eligible cells. Positive means greater power on the later date; "
                "negative means lower power. The physical cause is unverified.")
    if question == GUIDE_QUESTIONS[1]:
        return ("These are PROVISIONAL NISAR observations. Provider QA and original HDF5 metadata are incompletely "
                "inspected; subset fidelity and empirical physical co-registration remain unverified. Residual "
                "radiometric banding and RFI are possible. Matching grids and local hashes do not resolve those "
                "limitations or independently confirm an environmental event.")
    if question == GUIDE_QUESTIONS[2]:
        if selected is None:
            return "Select a row and column in Investigate Science. No cell measurement has been selected for this explanation."
        cell = pixel(data, *selected)
        location = f"Row {selected[0]}, column {selected[1]} has declared-grid centre E {cell['easting_m']:.1f}, N {cell['northing_m']:.1f} m (EPSG:32644). "
        if not cell['eligible']:
            return location + "It is excluded: " + cell['primary_exclusion_reason'] + ". No saved change value is supplied."
        return location + (f"Its saved change is {cell['saved_change_db']:+.9f} dB. This is a measured radar-response "
                           "difference, not a validated physical classification.")
    state = trust['local_integrity'] if trust is not None else 'NOT_EVALUATED_IN_THIS_EXPLANATION'
    return (f"Current local evidence evaluation: {state}. Byte restoration replaces only the isolated derived copy; "
            "rebuilding regenerates its statistics from verified change/support inputs and checks the result. "
            "Neither operation proves NASA authenticity, independent calibration or environmental causation. "
            "Scientific quality remains conditional and physical cause remains unverified.")


def guide_panel(data: ExplorerData, selected: tuple[int, int] | None, trust: dict | None = None) -> None:
    import streamlit as st
    with st.expander("Investigation guide · evidence-grounded, no AI service"):
        st.caption("Deterministic explanations from this investigation. No external model, transmission or invented answer.")
        chosen = st.session_state.get('uix_guide_question', GUIDE_QUESTIONS[0])
        for offset in (0, 2):
            for column, question in zip(st.columns(2), GUIDE_QUESTIONS[offset:offset+2]):
                if column.button(question, key='guide_' + str(GUIDE_QUESTIONS.index(question)), width='stretch'):
                    chosen = question
        st.session_state['uix_guide_question'] = chosen
        st.markdown('**' + chosen + '**')
        st.write(guide_answer(data, chosen, selected, trust))
