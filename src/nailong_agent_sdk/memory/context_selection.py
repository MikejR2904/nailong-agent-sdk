# Copyright (c) 2026 David Michael Indraputra

"""Deterministic stage-to-specification pruning and source-grounded selection."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..specifications.documents import DocumentNode, DocumentTree, SpecificationCategory
from ..specifications.preprocessing import keywords_from_task


class DesignStage(StrEnum):
    ARCHITECTURE_EXPLORATION = "architecture-exploration"
    RTL_DEVELOPMENT = "rtl-development"
    RTL_VERIFICATION = "rtl-verification"
    SYNTHESIS_DFT = "synthesis-dft"
    PHYSICAL_DESIGN = "physical-design"
    FIRMWARE_DEVELOPMENT = "firmware-development"
    SAFETY_SECURITY_ANALYSIS = "safety-security-analysis"
    SIGNOFF = "signoff"


STAGE_CATEGORIES: dict[DesignStage, set[SpecificationCategory]] = {
    DesignStage.ARCHITECTURE_EXPLORATION: {
        SpecificationCategory.FUNCTIONAL,
        SpecificationCategory.ARCHITECTURAL,
        SpecificationCategory.PPA,
        SpecificationCategory.PDK,
        SpecificationCategory.SAFETY_SECURITY,
        SpecificationCategory.ASSUMPTIONS,
    },
    DesignStage.RTL_DEVELOPMENT: {
        SpecificationCategory.FUNCTIONAL,
        SpecificationCategory.ARCHITECTURAL,
        SpecificationCategory.INTERFACE,
        SpecificationCategory.PPA,
        SpecificationCategory.VERIFICATION,
        SpecificationCategory.ASSUMPTIONS,
    },
    DesignStage.RTL_VERIFICATION: {
        SpecificationCategory.FUNCTIONAL,
        SpecificationCategory.INTERFACE,
        SpecificationCategory.VERIFICATION,
        SpecificationCategory.ASSUMPTIONS,
    },
    DesignStage.SYNTHESIS_DFT: {
        SpecificationCategory.ARCHITECTURAL,
        SpecificationCategory.INTERFACE,
        SpecificationCategory.PPA,
        SpecificationCategory.PDK,
        SpecificationCategory.VERIFICATION,
        SpecificationCategory.ASSUMPTIONS,
    },
    DesignStage.PHYSICAL_DESIGN: {
        SpecificationCategory.ARCHITECTURAL,
        SpecificationCategory.INTERFACE,
        SpecificationCategory.PPA,
        SpecificationCategory.PDK,
        SpecificationCategory.ASSUMPTIONS,
    },
    DesignStage.FIRMWARE_DEVELOPMENT: {
        SpecificationCategory.FUNCTIONAL,
        SpecificationCategory.ARCHITECTURAL,
        SpecificationCategory.INTERFACE,
        SpecificationCategory.ASSUMPTIONS,
    },
    DesignStage.SAFETY_SECURITY_ANALYSIS: {
        SpecificationCategory.FUNCTIONAL,
        SpecificationCategory.ARCHITECTURAL,
        SpecificationCategory.SAFETY_SECURITY,
        SpecificationCategory.ASSUMPTIONS,
    },
    DesignStage.SIGNOFF: {
        SpecificationCategory.PPA,
        SpecificationCategory.PDK,
        SpecificationCategory.VERIFICATION,
        SpecificationCategory.ASSUMPTIONS,
    },
}


class SelectedContext(StrictModel):
    stage: DesignStage
    selected_document_ids: list[str]
    nodes: list[DocumentNode]
    selection_reasons: dict[str, list[str]] = Field(default_factory=dict)


class TaskAwareContextSelector:
    """Prune by stage before exact pointer/keyword matching; never load all specs."""

    def select(
        self,
        trees: list[DocumentTree],
        stage: DesignStage,
        task_text: str,
        scope_pointers: list[str] = (),
    ) -> SelectedContext:
        categories = STAGE_CATEGORIES[stage]
        keywords = keywords_from_task(task_text)
        nodes: list[DocumentNode] = []
        document_ids: list[str] = []
        reasons: dict[str, list[str]] = {}
        lowered_pointers = [pointer.lower() for pointer in scope_pointers if pointer.strip()]
        for tree in trees:
            if tree.category not in categories:
                continue
            matches = []
            matched_pointer = False
            matched_keyword = False
            for node in tree.nodes:
                serialized = str(node.content).lower()
                location = node.source.location.lower()
                exact = any(
                    pointer in location or pointer in serialized for pointer in lowered_pointers
                )
                keyword_hit = any(keyword in serialized for keyword in keywords)
                if exact or keyword_hit:
                    matches.append(node)
                matched_pointer = matched_pointer or exact
                matched_keyword = matched_keyword or keyword_hit
            if matches:
                document_ids.append(tree.document_id)
                nodes.extend(matches)
                reasons[tree.document_id] = (["scope-pointer"] if matched_pointer else []) + (
                    ["keyword-match"] if matched_keyword else []
                )
        return SelectedContext(
            stage=stage, selected_document_ids=document_ids, nodes=nodes, selection_reasons=reasons
        )

    def select_verified_retrieval_nodes(
        self,
        trees: list[DocumentTree],
        stage: DesignStage,
        verified_nodes: list[DocumentNode],
    ) -> SelectedContext:
        """Admit only locally verified retrieval references within the stage matrix.

        A retrieval backend may rank candidates, but this method receives nodes
        only after their document ID, node ID, and ``SourceRef`` hash/location
        have matched the frozen local trees. It never consumes backend text.
        """

        allowed_categories = STAGE_CATEGORIES[stage]
        allowed = {
            (tree.document_id, node.node_id, node.source.source_hash, node.source.location)
            for tree in trees
            if tree.category in allowed_categories
            for node in tree.nodes
        }
        selected = [
            node
            for node in verified_nodes
            if (
                node.source.document_id,
                node.node_id,
                node.source.source_hash,
                node.source.location,
            )
            in allowed
        ]
        document_ids = sorted({node.source.document_id for node in selected})
        return SelectedContext(
            stage=stage,
            selected_document_ids=document_ids,
            nodes=selected,
            selection_reasons={
                document_id: ["verified-retrieval-reference"] for document_id in document_ids
            },
        )
