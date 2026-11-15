"""Decision implications from real dependency edges, never hazard predictions."""
from .earthbom import EarthBOM


def decision_impact(bom: EarthBOM, identity: str, state: dict) -> dict:
    node=bom.node(identity)
    if identity not in state.get('artifact_integrity',{}):
        raise ValueError('Selected artifact is absent from the verified evidence state')
    descendants=bom.descendants(identity)
    direct=tuple(n.id for n in bom.nodes if identity in n.parents)
    flagged=set(state.get('failed_artifacts',())) | set(state.get('requires_revalidation',()))
    affected=tuple(i for i in (identity,*descendants) if i in flagged)
    reports=tuple(n.id for n in bom.nodes if n.id in (identity,*descendants) and n.kind=='runtime_report')
    return dict(schema_version='1.0',selected_artifact=identity,selected_kind=node.kind,
                direct_dependents=list(direct),downstream_artifacts=list(descendants),
                currently_flagged=list(affected),dependent_reports=list(reports),
                recorded_consequence='Dependent evidence requires revalidation' if affected else 'No local integrity failure currently flags this dependency path',
                hypothetical_decision_consequence=(
                    'A person relying on a flagged report should pause reliance on the affected derived evidence until its dependencies are revalidated.'
                    if affected and reports else
                    'Any future decision relying on this evidence must retain the unresolved scientific and physical-cause limitations.'),
                decision_status='HYPOTHETICAL_GUIDANCE_NOT_AN_OPERATIONAL_ALERT',
                evidence_vs_decision='Artifact relationships are recorded dependencies; human decision implications are explicit hypothetical guidance, not observed impacts.',
                scientific_quality=state['scientific_quality'],physical_cause=state['environmental_interpretation'])
