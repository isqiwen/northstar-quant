"""Governed immutable artifact publishing for local factor-mining bundles.

This is deliberately a research-domain adapter around ``ArtifactStore``.  It
keeps data independent of research while ensuring a hash-only local command can
reconstruct real in-memory parents for immutable lineage without ever accepting
an arbitrary filesystem path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import cast

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    content_sha256,
    derived_identity_hash,
    lineage_hash,
    require_sha256,
    snapshot_lineage_hash,
)
from northstar_quant.data.artifacts.immutable_store import (
    ArtifactValue,
    ArtifactStore,
    ArtifactStoreError,
    StoredArtifact,
)
from northstar_quant.data.contracts.data_domain import (
    Artifact,
    ArtifactMetadata,
    ArtifactSnapshot,
    DataLineage,
    DataSource,
    DerivedArtifact,
    ArtifactProvenance,
    ArtifactKind,
)
from northstar_quant.research.factor_mining.run_bundle import (
    GovernedResearchArtifactKind,
    LocalFactorMiningCampaignDeclaration,
    LocalFactorMiningRunBundle,
    LocalFactorMiningRunBundleError,
    LocalFactorMiningRunConfig,
    LocalFactorMiningRunManifest,
    decode_factor_research_oos_robustness_proof,
    encode_factor_research_oos_robustness_proof,
    project_factor_research_oos_robustness_proof,
)
from northstar_quant.research.factor_mining.models import (
    CandidateValidationStatus,
    FactorMiningError,
)
from northstar_quant.research.factor_mining.protocol import (
    FactorMiningSelectionCommitment,
    FactorMiningSelectionDisposition,
    FactorMiningSelectionRecord,
)
from northstar_quant.research.factor_mining.validator import validate_factor_candidate
from northstar_quant.research.factors.models import FactorPipelineConfig


__all__ = [
    "LoadedLocalFactorMiningRunBundle",
    "LoadedLocalFactorMiningCampaignDeclaration",
    "PublishedLocalFactorMiningArtifact",
    "LocalFactorMiningArtifactBundleError",
    "LocalFactorMiningArtifactBundleStore",
]


_GOVERNED_ARTIFACT_FORMAT = "northstar.local-factor-mining-artifact.v1"
_BUNDLE_ARTIFACT_FORMAT = "northstar.local-factor-mining-run-bundle.v1"
_CAMPAIGN_DECLARATION_ARTIFACT_FORMAT = (
    "northstar.local-factor-mining-campaign-declaration.v1"
)
_MANIFEST_ARTIFACT_FORMAT = "northstar.local-factor-mining-run-manifest.v1"
_MANIFEST_ARTIFACT_KIND = "manifest"
_EVIDENCE_PREDECESSORS: dict[
    GovernedResearchArtifactKind, frozenset[GovernedResearchArtifactKind]
] = {
    GovernedResearchArtifactKind.EXPOSURES: frozenset(),
    GovernedResearchArtifactKind.WEIGHTS: frozenset(
        (GovernedResearchArtifactKind.EXPOSURES,)
    ),
    GovernedResearchArtifactKind.ANALYSES: frozenset(
        (
            GovernedResearchArtifactKind.EXPOSURES,
            GovernedResearchArtifactKind.WEIGHTS,
        )
    ),
    GovernedResearchArtifactKind.DISCOVERY_EVIDENCE: frozenset(
        (GovernedResearchArtifactKind.ANALYSES,)
    ),
    GovernedResearchArtifactKind.SELECTION_EVIDENCE: frozenset(
        (GovernedResearchArtifactKind.DISCOVERY_EVIDENCE,)
    ),
    GovernedResearchArtifactKind.OOS_EVIDENCE: frozenset(
        (GovernedResearchArtifactKind.SELECTION_EVIDENCE,)
    ),
    # REPORT is handled separately because OOS evidence is validly absent when
    # discovery selects no candidate.
    GovernedResearchArtifactKind.REPORT: frozenset(),
}


class LocalFactorMiningArtifactBundleError(RuntimeError):
    """A governed local research artifact cannot safely be published or replayed."""


@dataclass(frozen=True, slots=True)
class LoadedLocalFactorMiningRunBundle:
    """A verified input declaration and its immutable parent artifact."""

    bundle: LocalFactorMiningRunBundle
    stored: StoredArtifact
    artifact: DerivedArtifact


@dataclass(frozen=True, slots=True)
class LoadedLocalFactorMiningCampaignDeclaration:
    """A verified receipt-free campaign declaration and its immutable parent."""

    declaration: LocalFactorMiningCampaignDeclaration
    stored: StoredArtifact
    artifact: DerivedArtifact


@dataclass(frozen=True, slots=True)
class PublishedLocalFactorMiningArtifact:
    """One immutable evidence output plus the value needed for downstream lineage."""

    stored: StoredArtifact
    artifact: DerivedArtifact


@dataclass(frozen=True, slots=True)
class _LoadedLocalFactorMiningEvidence:
    """A semantically verified evidence role and its immutable storage record."""

    kind: GovernedResearchArtifactKind
    stored: StoredArtifact
    content: dict[str, object]


@dataclass(frozen=True, slots=True)
class _OOSRobustnessBinding:
    """The decoded OOS proof values that must also appear in release evidence."""

    candidate_id: str
    candidate_hash: str
    factor_definition_hash: str
    lookahead_certificate_hash: str
    pipeline_config_hash: str
    run_manifest_hash: str


@dataclass(frozen=True, slots=True)
class _PreparedLocalFactorMiningArtifact:
    """A deterministic derived artifact projection that has not been written yet."""

    source: DataSource
    artifact: DerivedArtifact
    payload: bytes
    lineage: DataLineage
    snapshot: ArtifactSnapshot
    lineage_snapshot_hash: str


class LocalFactorMiningArtifactBundleStore:
    """Publish/load only fixed local factor-mining inputs and research evidence.

    It has no ``latest`` lookup, no path input, no delete/cleanup operation and
    no knowledge of broker, scheduler, approval, or live configuration.
    """

    __slots__ = ("_artifact_store",)

    def __init__(self, *, artifact_store: ArtifactStore) -> None:
        if type(artifact_store) is not ArtifactStore:
            raise LocalFactorMiningArtifactBundleError(
                "artifact_store must be an exact ArtifactStore"
            )
        self._artifact_store = artifact_store

    def publish_definition(
        self,
        *,
        bundle: LocalFactorMiningRunBundle,
    ) -> PublishedLocalFactorMiningArtifact:
        """Publish one typed run declaration derived from all exact input snapshots."""

        if type(bundle) is not LocalFactorMiningRunBundle:
            raise LocalFactorMiningArtifactBundleError(
                "bundle must be an exact LocalFactorMiningRunBundle"
            )
        parents, source = self._dataset_parents(bundle)
        return self._publish_prepared(
            self._prepare(
                artifact_key="definition",
                payload=bundle.to_bytes(),
                bundle=bundle,
                config=bundle.config,
                parents=parents,
                source=source,
            )
        )

    def publish_campaign_declaration(
        self,
        *,
        declaration: LocalFactorMiningCampaignDeclaration,
    ) -> PublishedLocalFactorMiningArtifact:
        """Publish the receipt-free declaration required before durable generation.

        The normal run bundle remains a post-receipt research input.  This
        sibling artifact gives the durable campaign runner a hash-addressed
        declaration it can reserve before calling any candidate generator.
        """

        if type(declaration) is not LocalFactorMiningCampaignDeclaration:
            raise LocalFactorMiningArtifactBundleError(
                "declaration must be an exact LocalFactorMiningCampaignDeclaration"
            )
        parents, source = self._dataset_parents(declaration)
        return self._publish_prepared(
            self._prepare_campaign_declaration(
                declaration=declaration,
                parents=parents,
                source=source,
            )
        )

    def load_definition(self, snapshot_hash: str) -> LoadedLocalFactorMiningRunBundle:
        """Load a bundle only after record, blob, authorization and parent checks pass."""

        try:
            stored = self._artifact_store.load_artifact(snapshot_hash)
            if stored.snapshot.kind is not ArtifactKind.DERIVED:
                raise LocalFactorMiningArtifactBundleError(
                    "local research bundle must be a governed derived artifact"
                )
            bundle = LocalFactorMiningRunBundle.from_bytes(
                self._artifact_store.read_payload(stored.snapshot.snapshot_hash)
            )
            parents, source = self._dataset_parents(bundle)
            expected_parent_hashes = tuple(
                self._snapshot_hash(item) for item in parents
            )
            if stored.parent_snapshot_hashes != expected_parent_hashes:
                raise LocalFactorMiningArtifactBundleError(
                    "bundle artifact lineage does not exactly bind its DatasetVersion inputs"
                )
            self._assert_canonical_parent_order(stored=stored)
            self._assert_governed_metadata(
                stored=stored,
                bundle=bundle,
                artifact_key="definition",
                source=source,
            )
            restored = self._artifact_store.load_artifact_value(stored.snapshot.snapshot_hash)
        except (
            ArtifactStoreError,
            LocalFactorMiningRunBundleError,
            ValueError,
        ) as exc:
            raise LocalFactorMiningArtifactBundleError(
                "local research bundle is unverified or cannot be replayed"
            ) from exc
        if type(restored) is not DerivedArtifact:
            raise LocalFactorMiningArtifactBundleError(
                "bundle artifact did not reconstruct as an exact derived artifact"
            )
        return LoadedLocalFactorMiningRunBundle(bundle=bundle, stored=stored, artifact=restored)

    def load_campaign_declaration(
        self,
        snapshot_hash: str,
    ) -> LoadedLocalFactorMiningCampaignDeclaration:
        """Load a pre-generation declaration after its data lineage re-verifies."""

        try:
            stored = self._artifact_store.load_artifact(snapshot_hash)
            if stored.snapshot.kind is not ArtifactKind.DERIVED:
                raise LocalFactorMiningArtifactBundleError(
                    "campaign declaration must be a governed derived artifact"
                )
            declaration = LocalFactorMiningCampaignDeclaration.from_bytes(
                self._artifact_store.read_payload(stored.snapshot.snapshot_hash)
            )
            parents, source = self._dataset_parents(declaration)
            expected_parent_hashes = tuple(self._snapshot_hash(item) for item in parents)
            if stored.parent_snapshot_hashes != expected_parent_hashes:
                raise LocalFactorMiningArtifactBundleError(
                    "campaign declaration lineage does not exactly bind DatasetVersion inputs"
                )
            self._assert_canonical_parent_order(stored=stored)
            self._assert_campaign_declaration_metadata(
                stored=stored,
                declaration=declaration,
                source=source,
            )
            restored = self._artifact_store.load_artifact_value(stored.snapshot.snapshot_hash)
        except (
            ArtifactStoreError,
            LocalFactorMiningRunBundleError,
            ValueError,
        ) as exc:
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration is unverified or cannot be replayed"
            ) from exc
        if type(restored) is not DerivedArtifact:
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration did not reconstruct as an exact derived artifact"
            )
        return LoadedLocalFactorMiningCampaignDeclaration(
            declaration=declaration,
            stored=stored,
            artifact=restored,
        )

    def _publish_evidence(
        self,
        *,
        kind: GovernedResearchArtifactKind,
        bundle: LocalFactorMiningRunBundle,
        bundle_artifact: DerivedArtifact,
        bundle_source: DataSource,
        payload: Mapping[str, object],
        upstream: tuple[DerivedArtifact, ...] = (),
    ) -> PublishedLocalFactorMiningArtifact:
        """Publish an evidence role with content, lineage and retention metadata."""

        return self._publish_prepared(
            self._prepare_evidence(
                kind=kind,
                bundle=bundle,
                bundle_artifact=bundle_artifact,
                bundle_source=bundle_source,
                payload=payload,
                upstream=upstream,
            )
        )

    def _prepare_evidence(
        self,
        *,
        kind: GovernedResearchArtifactKind,
        bundle: LocalFactorMiningRunBundle,
        bundle_artifact: DerivedArtifact,
        bundle_source: DataSource,
        payload: Mapping[str, object],
        upstream: tuple[DerivedArtifact, ...] = (),
    ) -> _PreparedLocalFactorMiningArtifact:
        """Project one evidence artifact without writing to the immutable store.

        Replay uses this exact projection path to compare the full resulting
        evidence graph before it is allowed to return a prior manifest.
        """

        if type(kind) is not GovernedResearchArtifactKind:
            raise LocalFactorMiningArtifactBundleError("evidence kind is unsupported")
        if type(bundle) is not LocalFactorMiningRunBundle:
            raise LocalFactorMiningArtifactBundleError("bundle must be exact")
        if type(bundle_artifact) is not DerivedArtifact:
            raise LocalFactorMiningArtifactBundleError("bundle_artifact must be exact DerivedArtifact")
        if type(bundle_source) is not DataSource:
            raise LocalFactorMiningArtifactBundleError("bundle_source must be exact DataSource")
        if not isinstance(payload, Mapping):
            raise LocalFactorMiningArtifactBundleError("evidence payload must be a mapping")
        if not all(type(item) is DerivedArtifact for item in upstream):
            raise LocalFactorMiningArtifactBundleError("evidence upstream must be exact DerivedArtifact values")
        wrapped: dict[str, object] = {
            "content": dict(payload),
            "format": _GOVERNED_ARTIFACT_FORMAT,
            "kind": kind.value,
            "research_only": True,
            "retention": bundle.config.retention_mapping(),
            "run_bundle_hash": bundle.bundle_hash,
        }
        return self._prepare(
            artifact_key=kind.value,
            payload=_canonical_json_bytes(wrapped),
            bundle=bundle,
            config=bundle.config,
            parents=(bundle_artifact, *upstream),
            source=bundle_source,
        )

    def _publish_manifest(
        self,
        *,
        bundle: LocalFactorMiningRunBundle,
        bundle_artifact: DerivedArtifact,
        bundle_source: DataSource,
        manifest: LocalFactorMiningRunManifest,
        report_artifact: DerivedArtifact,
    ) -> PublishedLocalFactorMiningArtifact:
        """Publish the final hash-only manifest after all report evidence is immutable."""

        return self._publish_prepared(
            self._prepare_manifest(
                bundle=bundle,
                bundle_artifact=bundle_artifact,
                bundle_source=bundle_source,
                manifest=manifest,
                report_artifact=report_artifact,
            )
        )

    def _prepare_manifest(
        self,
        *,
        bundle: LocalFactorMiningRunBundle,
        bundle_artifact: DerivedArtifact,
        bundle_source: DataSource,
        manifest: LocalFactorMiningRunManifest,
        report_artifact: DerivedArtifact,
    ) -> _PreparedLocalFactorMiningArtifact:
        """Project the terminal manifest artifact without publishing it."""

        if type(manifest) is not LocalFactorMiningRunManifest:
            raise LocalFactorMiningArtifactBundleError("manifest must be exact")
        if manifest.bundle_hash != bundle.bundle_hash:
            raise LocalFactorMiningArtifactBundleError("manifest does not bind this exact bundle")
        if type(report_artifact) is not DerivedArtifact:
            raise LocalFactorMiningArtifactBundleError("report_artifact must be exact DerivedArtifact")
        return self._prepare(
            artifact_key=_MANIFEST_ARTIFACT_KIND,
            payload=manifest.to_bytes(),
            bundle=bundle,
            config=bundle.config,
            parents=(bundle_artifact, report_artifact),
            source=bundle_source,
        )

    def load_manifest(self, snapshot_hash: str) -> LocalFactorMiningRunManifest:
        """Read a manifest only when its complete governed evidence graph verifies."""

        try:
            stored = self._artifact_store.load_artifact(snapshot_hash)
            if stored.snapshot.kind is not ArtifactKind.DERIVED:
                raise LocalFactorMiningArtifactBundleError(
                    "local research manifest must be a governed derived artifact"
                )
            manifest = LocalFactorMiningRunManifest.from_bytes(
                self._artifact_store.read_payload(stored.snapshot.snapshot_hash)
            )
            self._assert_canonical_parent_order(stored=stored)
            bundle = self._load_direct_bundle_parent(stored=stored)
            self._assert_governed_metadata(
                stored=stored,
                bundle=bundle.bundle,
                artifact_key=_MANIFEST_ARTIFACT_KIND,
                source=bundle.stored.source,
            )
            self._assert_manifest_binds_bundle(manifest=manifest, bundle=bundle)
            evidence_by_kind: dict[
                GovernedResearchArtifactKind, _LoadedLocalFactorMiningEvidence
            ] = {}
            for reference in manifest.artifacts:
                evidence = self._load_evidence(
                    snapshot_hash=reference.snapshot_hash,
                    bundle=bundle,
                    expected_kind=reference.kind,
                )
                if (
                    evidence.stored.snapshot.snapshot_hash != reference.snapshot_hash
                    or evidence.stored.snapshot.content_hash != reference.content_hash
                    or evidence.stored.lineage_snapshot_hash != reference.lineage_snapshot_hash
                ):
                    raise LocalFactorMiningArtifactBundleError(
                        "manifest evidence reference does not match its immutable artifact"
                    )
                evidence_by_kind[reference.kind] = evidence
            report = evidence_by_kind[GovernedResearchArtifactKind.REPORT]
            self._assert_manifest_references_form_complete_graph(
                manifest=manifest,
                bundle=bundle,
                evidence_by_kind=evidence_by_kind,
            )
            expected_manifest_parents = _canonical_snapshot_hashes(
                (
                    (
                        bundle.stored.snapshot.snapshot_hash,
                        bundle.stored.snapshot.content_hash,
                    ),
                    (
                        report.stored.snapshot.snapshot_hash,
                        report.stored.snapshot.content_hash,
                    ),
                )
            )
            if stored.parent_snapshot_hashes != expected_manifest_parents:
                raise LocalFactorMiningArtifactBundleError(
                    "manifest lineage must bind exactly its definition and report evidence"
                )
            self._assert_manifest_evidence_bindings(
                manifest=manifest,
                bundle=bundle.bundle,
                evidence_by_kind=evidence_by_kind,
            )
            return manifest
        except (
            ArtifactStoreError,
            LocalFactorMiningRunBundleError,
            ValueError,
        ) as exc:
            raise LocalFactorMiningArtifactBundleError(
                "local research manifest is unverified or cannot be replayed"
            ) from exc

    def inspect(self, snapshot_hash: str) -> dict[str, object]:
        """Return a bounded view of a verified definition, evidence object, or manifest."""

        try:
            stored = self._artifact_store.load_artifact(snapshot_hash)
            if stored.snapshot.kind is not ArtifactKind.DERIVED:
                raise LocalFactorMiningArtifactBundleError(
                    "inspect accepts only governed derived research artifacts"
                )
            payload = self._artifact_store.read_payload(stored.snapshot.snapshot_hash)
        except ArtifactStoreError as exc:
            raise LocalFactorMiningArtifactBundleError(
                "inspect requires a verified immutable research artifact"
            ) from exc
        artifact_format = _governed_payload_format(payload)
        if artifact_format == _BUNDLE_ARTIFACT_FORMAT:
            definition = self.load_definition(stored.snapshot.snapshot_hash)
            return {
                "artifact_snapshot_hash": stored.snapshot.snapshot_hash,
                "bundle_hash": definition.bundle.bundle_hash,
                "dataset_version_hashes": list(definition.bundle.dataset_version_hashes),
                "decision_replay_plan_hash": definition.bundle.plan.schedule_hash,
                "kind": "definition",
                "retention": definition.bundle.config.retention_mapping(),
                "research_only": True,
            }
        if artifact_format == _MANIFEST_ARTIFACT_FORMAT:
            manifest = self.load_manifest(stored.snapshot.snapshot_hash)
            return {
                "artifact_snapshot_hash": stored.snapshot.snapshot_hash,
                "artifact_snapshot_hashes": [item.snapshot_hash for item in manifest.artifacts],
                "bundle_hash": manifest.bundle_hash,
                "kind": "manifest",
                "manifest_hash": manifest.manifest_hash,
                "research_only": True,
                "result_hash": manifest.result_hash,
            }
        if artifact_format == _GOVERNED_ARTIFACT_FORMAT:
            bundle = self._load_direct_bundle_parent(stored=stored)
            evidence = self._load_evidence(
                snapshot_hash=stored.snapshot.snapshot_hash,
                bundle=bundle,
            )
            return {
                "artifact_snapshot_hash": stored.snapshot.snapshot_hash,
                "bundle_hash": bundle.bundle.bundle_hash,
                "kind": evidence.kind.value,
                "retention": bundle.bundle.config.retention_mapping(),
                "research_only": True,
            }
        raise LocalFactorMiningArtifactBundleError(
            "research artifact payload is not a governed local factor-mining artifact"
        )

    def _load_direct_bundle_parent(
        self,
        *,
        stored: StoredArtifact,
    ) -> LoadedLocalFactorMiningRunBundle:
        """Find the sole direct definition parent of a governed artifact."""

        candidates: list[LoadedLocalFactorMiningRunBundle] = []
        for parent_snapshot_hash in stored.parent_snapshot_hashes:
            try:
                candidate = self.load_definition(parent_snapshot_hash)
            except LocalFactorMiningArtifactBundleError:
                continue
            candidates.append(candidate)
        if len(candidates) != 1:
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact must have exactly one verified definition parent"
            )
        return candidates[0]

    def _load_evidence(
        self,
        *,
        snapshot_hash: str,
        bundle: LoadedLocalFactorMiningRunBundle,
        expected_kind: GovernedResearchArtifactKind | None = None,
        visiting: frozenset[str] = frozenset(),
    ) -> _LoadedLocalFactorMiningEvidence:
        """Verify one role's envelope, metadata, bundle binding and direct lineage."""

        try:
            stored = self._artifact_store.load_artifact(snapshot_hash)
            if stored.snapshot.kind is not ArtifactKind.DERIVED:
                raise LocalFactorMiningArtifactBundleError(
                    "research evidence must be a governed derived artifact"
                )
            if stored.snapshot.snapshot_hash in visiting:
                raise LocalFactorMiningArtifactBundleError(
                    "research evidence lineage contains a cycle"
                )
            kind, content, retention, run_bundle_hash = _decode_governed_evidence_payload(
                self._artifact_store.read_payload(stored.snapshot.snapshot_hash)
            )
        except ArtifactStoreError as exc:
            raise LocalFactorMiningArtifactBundleError(
                "research evidence is unavailable, unauthorized, or corrupt"
            ) from exc
        if expected_kind is not None and kind is not expected_kind:
            raise LocalFactorMiningArtifactBundleError(
                "research evidence kind does not match its sealed reference"
            )
        if (
            run_bundle_hash != bundle.bundle.bundle_hash
            or retention != bundle.bundle.config.retention_mapping()
        ):
            raise LocalFactorMiningArtifactBundleError(
                "research evidence envelope does not exactly bind the sealed declaration"
            )
        self._assert_canonical_parent_order(stored=stored)
        self._assert_governed_metadata(
            stored=stored,
            bundle=bundle.bundle,
            artifact_key=kind.value,
            source=bundle.stored.source,
        )
        self._assert_evidence_common_bindings(content=content, bundle=bundle.bundle)

        definition_snapshot_hash = bundle.stored.snapshot.snapshot_hash
        if stored.parent_snapshot_hashes.count(definition_snapshot_hash) != 1:
            raise LocalFactorMiningArtifactBundleError(
                "research evidence must directly bind its exact definition artifact"
            )
        predecessor_hashes = tuple(
            item
            for item in stored.parent_snapshot_hashes
            if item != definition_snapshot_hash
        )
        predecessors = tuple(
            self._load_evidence(
                snapshot_hash=item,
                bundle=bundle,
                visiting=visiting | {stored.snapshot.snapshot_hash},
            )
            for item in predecessor_hashes
        )
        self._assert_evidence_predecessors(kind=kind, predecessors=predecessors)
        return _LoadedLocalFactorMiningEvidence(
            kind=kind,
            stored=stored,
            content=content,
        )

    def _assert_governed_metadata(
        self,
        *,
        stored: StoredArtifact,
        bundle: LocalFactorMiningRunBundle,
        artifact_key: str,
        source: DataSource,
    ) -> None:
        """Require metadata that only the governed publisher can deterministically emit."""

        snapshot = stored.snapshot
        if snapshot.kind is not ArtifactKind.DERIVED or stored.lineage_snapshot_hash is None:
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact must have a derived lineage snapshot"
            )
        if stored.source != source:
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact source does not match the sealed definition"
            )
        if (
            snapshot.artifact_id
            != f"local_factor_mining_{bundle.bundle_hash[:16]}_{artifact_key}"
            or snapshot.source_id != source.source_id
            or snapshot.schema_version != bundle.config.output_schema_version
            or snapshot.transform_version != bundle.config.output_transform_version
        ):
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact metadata does not match the sealed bundle"
            )
        expected_provenance = ArtifactProvenance(
            source_id=source.source_id,
            source_reference=(
                f"northstar.local-factor-mining:{artifact_key}:{bundle.bundle_hash}"
            ),
            collection_method="deterministic_local_factor_research",
            attributes=(
                ("bundle_hash", bundle.bundle_hash),
                ("retention_days", str(bundle.config.retention_days)),
                ("retention_policy", bundle.config.config_id),
            ),
        )
        if snapshot.provenance != expected_provenance:
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact provenance does not match the sealed bundle"
            )
        try:
            parents = tuple(
                self._artifact_store.load_artifact_value(item)
                for item in stored.parent_snapshot_hashes
            )
        except ArtifactStoreError as exc:
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact parent values are unavailable"
            ) from exc
        if not parents:
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact must have immutable parents"
            )
        if (
            snapshot.acquired_at != max(item.acquired_at for item in parents)
            or snapshot.available_at != max(item.available_at for item in parents)
            or snapshot.quality_status
            != max(
                (item.quality_status for item in parents),
                key=lambda item: _quality_rank(item.value),
            )
        ):
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact PIT or quality metadata is inconsistent"
            )

    def _assert_canonical_parent_order(self, *, stored: StoredArtifact) -> None:
        """Require the deterministic parent order used by the sole publisher."""

        try:
            parent_values = tuple(
                self._artifact_store.load_artifact_value(item)
                for item in stored.parent_snapshot_hashes
            )
        except ArtifactStoreError as exc:
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact parents are unavailable"
            ) from exc
        expected = tuple(
            self._snapshot_hash(item) for item in _canonical_parents(parent_values)
        )
        if stored.parent_snapshot_hashes != expected:
            raise LocalFactorMiningArtifactBundleError(
                "governed research artifact parent order is not canonical"
            )

    @staticmethod
    def _assert_manifest_binds_bundle(
        *,
        manifest: LocalFactorMiningRunManifest,
        bundle: LoadedLocalFactorMiningRunBundle,
    ) -> None:
        declaration = bundle.bundle
        if (
            manifest.bundle_hash != declaration.bundle_hash
            or manifest.dataset_version_hashes != declaration.dataset_version_hashes
            or manifest.decision_replay_plan_hash != declaration.plan.schedule_hash
            or manifest.campaign_hash != declaration.campaign.campaign_hash
            or manifest.generation_receipt_hash != declaration.generation.receipt_hash
            or manifest.config_hash != declaration.config.config_hash
        ):
            raise LocalFactorMiningArtifactBundleError(
                "manifest does not exactly bind its sealed local research declaration"
            )

    @staticmethod
    def _assert_evidence_common_bindings(
        *,
        content: Mapping[str, object],
        bundle: LocalFactorMiningRunBundle,
    ) -> None:
        expected = {
            "campaign_hash": bundle.campaign.campaign_hash,
            "config_hash": bundle.config.config_hash,
            "dataset_version_hashes": list(bundle.dataset_version_hashes),
            "decision_replay_plan_hash": bundle.plan.schedule_hash,
            "generation_receipt_hash": bundle.generation.receipt_hash,
        }
        if any(content.get(key) != value for key, value in expected.items()):
            raise LocalFactorMiningArtifactBundleError(
                "research evidence content does not exactly bind the sealed declaration"
            )

    @staticmethod
    def _assert_evidence_predecessors(
        *,
        kind: GovernedResearchArtifactKind,
        predecessors: tuple[_LoadedLocalFactorMiningEvidence, ...],
    ) -> None:
        predecessor_kinds = tuple(item.kind for item in predecessors)
        predecessor_set = frozenset(predecessor_kinds)
        if len(predecessor_set) != len(predecessor_kinds):
            raise LocalFactorMiningArtifactBundleError(
                "research evidence cannot have duplicate predecessor roles"
            )
        if kind is GovernedResearchArtifactKind.REPORT:
            permitted = {
                frozenset((GovernedResearchArtifactKind.SELECTION_EVIDENCE,)),
                frozenset(
                    (
                        GovernedResearchArtifactKind.SELECTION_EVIDENCE,
                        GovernedResearchArtifactKind.OOS_EVIDENCE,
                    )
                ),
            }
            if predecessor_set not in permitted:
                raise LocalFactorMiningArtifactBundleError(
                    "report evidence has an invalid governed predecessor graph"
                )
            return
        expected = _EVIDENCE_PREDECESSORS[kind]
        if predecessor_set != expected or len(predecessor_kinds) != len(expected):
            raise LocalFactorMiningArtifactBundleError(
                "research evidence has an invalid governed predecessor graph"
            )

    @staticmethod
    def _assert_manifest_evidence_bindings(
        *,
        manifest: LocalFactorMiningRunManifest,
        bundle: LocalFactorMiningRunBundle,
        evidence_by_kind: Mapping[
            GovernedResearchArtifactKind, _LoadedLocalFactorMiningEvidence
        ],
    ) -> None:
        discovery = _nested_mapping(
            evidence_by_kind[GovernedResearchArtifactKind.DISCOVERY_EVIDENCE].content,
            "discovery",
        )
        selection = _nested_mapping(
            evidence_by_kind[GovernedResearchArtifactKind.SELECTION_EVIDENCE].content,
            "selection_commitment",
        )
        report = evidence_by_kind[GovernedResearchArtifactKind.REPORT].content
        _assert_exact_hash(
            mapping=discovery,
            field_name="discovery_result_hash",
            expected=manifest.discovery_result_hash,
        )
        _assert_exact_hash(
            mapping=selection,
            field_name="commitment_hash",
            expected=manifest.selection_commitment_hash,
        )
        selection_commitment = _decode_sealed_selection_commitment(
            selection=selection,
            bundle=bundle,
            manifest=manifest,
        )
        if manifest.oos_release_hash is not None:
            oos = _nested_mapping(
                evidence_by_kind[GovernedResearchArtifactKind.OOS_EVIDENCE].content,
                "oos_release",
            )
            _assert_exact_hash(
                mapping=oos,
                field_name="release_hash",
                expected=manifest.oos_release_hash,
            )
        if (
            report.get("discovery_result_hash") != manifest.discovery_result_hash
            or report.get("selection_commitment_hash") != manifest.selection_commitment_hash
            or report.get("oos_release_hash") != manifest.oos_release_hash
        ):
            raise LocalFactorMiningArtifactBundleError(
                "report evidence does not exactly bind the manifest decision evidence"
            )
        _assert_report_robustness_bindings(
            bundle=bundle,
            evidence_by_kind=evidence_by_kind,
            selection_commitment=selection_commitment,
        )

    @staticmethod
    def _assert_manifest_references_form_complete_graph(
        *,
        manifest: LocalFactorMiningRunManifest,
        bundle: LoadedLocalFactorMiningRunBundle,
        evidence_by_kind: Mapping[
            GovernedResearchArtifactKind, _LoadedLocalFactorMiningEvidence
        ],
    ) -> None:
        """Bind every manifest reference to the one exact evidence DAG it names."""

        references = {item.kind: item for item in manifest.artifacts}
        for kind, evidence in evidence_by_kind.items():
            predecessors: tuple[GovernedResearchArtifactKind, ...]
            if kind is GovernedResearchArtifactKind.REPORT:
                predecessors = (
                    (
                        GovernedResearchArtifactKind.SELECTION_EVIDENCE,
                        GovernedResearchArtifactKind.OOS_EVIDENCE,
                    )
                    if GovernedResearchArtifactKind.OOS_EVIDENCE in references
                    else (GovernedResearchArtifactKind.SELECTION_EVIDENCE,)
                )
            else:
                predecessors = tuple(_EVIDENCE_PREDECESSORS[kind])
            expected = _canonical_snapshot_hashes(
                (
                    (
                        bundle.stored.snapshot.snapshot_hash,
                        bundle.stored.snapshot.content_hash,
                    ),
                    *(
                        (
                            references[predecessor].snapshot_hash,
                            references[predecessor].content_hash,
                        )
                        for predecessor in predecessors
                    ),
                )
            )
            if evidence.stored.parent_snapshot_hashes != expected:
                raise LocalFactorMiningArtifactBundleError(
                    "manifest evidence references do not form one complete governed graph"
                )

    def _dataset_parents(
        self,
        bundle: LocalFactorMiningRunBundle | LocalFactorMiningCampaignDeclaration,
    ) -> tuple[tuple[ArtifactValue, ...], DataSource]:
        """Resolve every DatasetVersion exactly; no selector, path, or current view exists."""

        values_by_content_hash: dict[str, ArtifactValue] = {}
        sources_by_content_hash: dict[str, DataSource] = {}
        try:
            for version_hash in bundle.dataset_version_hashes:
                dataset = self._artifact_store.replay_dataset_version(version_hash)
                if dataset.dataset_version.version_hash != version_hash:
                    raise LocalFactorMiningArtifactBundleError(
                        "DatasetVersion replay identity does not match the sealed bundle"
                    )
                for replay in dataset.artifacts:
                    snapshot_hash = replay.stored.snapshot.snapshot_hash
                    value = self._artifact_store.load_artifact_value(snapshot_hash)
                    content_hash = value.content_hash
                    existing = values_by_content_hash.get(content_hash)
                    if existing is not None and self._snapshot_hash(existing) != snapshot_hash:
                        raise LocalFactorMiningArtifactBundleError(
                            "DatasetVersion inputs reuse a content hash with different provenance"
                        )
                    values_by_content_hash[content_hash] = value
                    sources_by_content_hash[content_hash] = replay.stored.source
        except ArtifactStoreError as exc:
            raise LocalFactorMiningArtifactBundleError(
                "sealed DatasetVersion input is unavailable, unauthorized, or corrupt"
            ) from exc
        parents = tuple(value for _, value in sorted(values_by_content_hash.items()))
        if not parents:
            raise LocalFactorMiningArtifactBundleError("sealed bundle has no verified DatasetVersion input")
        source = sources_by_content_hash[parents[0].content_hash]
        return parents, source

    def _prepare_campaign_declaration(
        self,
        *,
        declaration: LocalFactorMiningCampaignDeclaration,
        parents: tuple[ArtifactValue, ...],
        source: DataSource,
    ) -> _PreparedLocalFactorMiningArtifact:
        """Project the one receipt-free declaration without publishing it."""

        if type(declaration) is not LocalFactorMiningCampaignDeclaration:
            raise LocalFactorMiningArtifactBundleError("campaign declaration must be exact")
        if not parents:
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration requires verified DatasetVersion inputs"
            )
        if type(source) is not DataSource:
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration source must be exact"
            )
        payload = declaration.to_bytes()
        canonical_parents = _canonical_parents(parents)
        lineage_inputs = tuple(cast(Artifact, item) for item in canonical_parents)
        available_at = max(item.available_at for item in canonical_parents)
        acquired_at = max(item.acquired_at for item in canonical_parents)
        if available_at < acquired_at:  # pragma: no cover - parent contracts guard this.
            available_at = acquired_at
        identity = declaration.declaration_hash
        metadata = ArtifactMetadata(
            artifact_id=f"local_factor_mining_campaign_{identity[:16]}_declaration",
            source_id=source.source_id,
            acquired_at=acquired_at,
            available_at=available_at,
            schema_version=declaration.config.output_schema_version,
            content_hash=content_sha256(
                payload,
                field_name="local factor-mining campaign declaration payload",
            ),
            transform_version=declaration.config.output_transform_version,
            quality_status=max(
                (item.quality_status for item in canonical_parents),
                key=lambda item: _quality_rank(item.value),
            ),
            provenance=ArtifactProvenance(
                source_id=source.source_id,
                source_reference=(
                    "northstar.local-factor-mining:campaign-declaration:"
                    f"{identity}"
                ),
                collection_method="deterministic_local_factor_research",
                attributes=(
                    ("declaration_hash", identity),
                    ("retention_days", str(declaration.config.retention_days)),
                    ("retention_policy", declaration.config.config_id),
                ),
            ),
        )
        artifact = DerivedArtifact(
            metadata=metadata,
            input_artifacts=lineage_inputs,
            derivation_identity=derived_identity_hash(
                (item.content_hash for item in canonical_parents),
                declaration.config.output_transform_version,
                declaration.config.output_schema_version,
            ),
        )
        lineage = DataLineage(
            output_artifact=cast(Artifact, artifact),
            input_artifacts=lineage_inputs,
            transform_version=declaration.config.output_transform_version,
            lineage_identity=lineage_hash(
                artifact.content_hash,
                (item.content_hash for item in canonical_parents),
                declaration.config.output_transform_version,
            ),
            recorded_at=available_at,
        )
        snapshot = ArtifactSnapshot.from_artifact(cast(Artifact, artifact))
        return _PreparedLocalFactorMiningArtifact(
            source=source,
            artifact=artifact,
            payload=payload,
            lineage=lineage,
            snapshot=snapshot,
            lineage_snapshot_hash=snapshot_lineage_hash(
                snapshot.snapshot_hash,
                tuple(self._snapshot_hash(item) for item in canonical_parents),
                declaration.config.output_transform_version,
            ),
        )

    def _assert_campaign_declaration_metadata(
        self,
        *,
        stored: StoredArtifact,
        declaration: LocalFactorMiningCampaignDeclaration,
        source: DataSource,
    ) -> None:
        """Verify the deterministic metadata/provenance for a declaration."""

        snapshot = stored.snapshot
        identity = declaration.declaration_hash
        if snapshot.kind is not ArtifactKind.DERIVED or stored.lineage_snapshot_hash is None:
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration must retain a derived lineage snapshot"
            )
        if stored.source != source:
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration source does not match its verified datasets"
            )
        if (
            snapshot.artifact_id
            != f"local_factor_mining_campaign_{identity[:16]}_declaration"
            or snapshot.source_id != source.source_id
            or snapshot.schema_version != declaration.config.output_schema_version
            or snapshot.transform_version != declaration.config.output_transform_version
        ):
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration metadata does not match its sealed declaration"
            )
        expected_provenance = ArtifactProvenance(
            source_id=source.source_id,
            source_reference=(
                "northstar.local-factor-mining:campaign-declaration:"
                f"{identity}"
            ),
            collection_method="deterministic_local_factor_research",
            attributes=(
                ("declaration_hash", identity),
                ("retention_days", str(declaration.config.retention_days)),
                ("retention_policy", declaration.config.config_id),
            ),
        )
        if snapshot.provenance != expected_provenance:
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration provenance does not match its sealed declaration"
            )
        try:
            parents = tuple(
                self._artifact_store.load_artifact_value(item)
                for item in stored.parent_snapshot_hashes
            )
        except ArtifactStoreError as exc:
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration parent values are unavailable"
            ) from exc
        if not parents:
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration must retain immutable parents"
            )
        if (
            snapshot.acquired_at != max(item.acquired_at for item in parents)
            or snapshot.available_at != max(item.available_at for item in parents)
            or snapshot.quality_status
            != max(
                (item.quality_status for item in parents),
                key=lambda item: _quality_rank(item.value),
            )
        ):
            raise LocalFactorMiningArtifactBundleError(
                "campaign declaration PIT or quality metadata is inconsistent"
            )

    def _prepare(
        self,
        *,
        artifact_key: str,
        payload: bytes,
        bundle: LocalFactorMiningRunBundle,
        config: LocalFactorMiningRunConfig,
        parents: tuple[ArtifactValue, ...],
        source: DataSource,
    ) -> _PreparedLocalFactorMiningArtifact:
        """Build the exact derived artifact/store identity without a side effect."""

        if not isinstance(payload, bytes):
            raise LocalFactorMiningArtifactBundleError("artifact payload must be bytes")
        if not parents:
            raise LocalFactorMiningArtifactBundleError("governed artifact requires verified parents")
        if type(source) is not DataSource:
            raise LocalFactorMiningArtifactBundleError("governed artifact source must be exact")
        canonical_parents = _canonical_parents(parents)
        lineage_inputs = tuple(cast(Artifact, item) for item in canonical_parents)
        available_at = max(item.available_at for item in canonical_parents)
        acquired_at = max(item.acquired_at for item in canonical_parents)
        if available_at < acquired_at:  # pragma: no cover - each artifact contract guards this.
            available_at = acquired_at
        metadata = ArtifactMetadata(
            artifact_id=(
                f"local_factor_mining_{bundle.bundle_hash[:16]}_{_safe_artifact_key(artifact_key)}"
            ),
            source_id=source.source_id,
            acquired_at=acquired_at,
            available_at=available_at,
            schema_version=config.output_schema_version,
            content_hash=content_sha256(payload, field_name="local research artifact payload"),
            transform_version=config.output_transform_version,
            quality_status=max(
                (item.quality_status for item in canonical_parents),
                key=lambda item: _quality_rank(item.value),
            ),
            provenance=ArtifactProvenance(
                source_id=source.source_id,
                source_reference=f"northstar.local-factor-mining:{artifact_key}:{bundle.bundle_hash}",
                collection_method="deterministic_local_factor_research",
                attributes=(
                    ("bundle_hash", bundle.bundle_hash),
                    ("retention_days", str(config.retention_days)),
                    ("retention_policy", config.config_id),
                ),
            ),
        )
        artifact = DerivedArtifact(
            metadata=metadata,
            input_artifacts=lineage_inputs,
            derivation_identity=derived_identity_hash(
                (item.content_hash for item in canonical_parents),
                config.output_transform_version,
                config.output_schema_version,
            ),
        )
        lineage = DataLineage(
            output_artifact=cast(Artifact, artifact),
            input_artifacts=lineage_inputs,
            transform_version=config.output_transform_version,
            lineage_identity=lineage_hash(
                artifact.content_hash,
                (item.content_hash for item in canonical_parents),
                config.output_transform_version,
            ),
            recorded_at=available_at,
        )
        snapshot = ArtifactSnapshot.from_artifact(cast(Artifact, artifact))
        parent_snapshot_hashes = tuple(
            self._snapshot_hash(item) for item in canonical_parents
        )
        return _PreparedLocalFactorMiningArtifact(
            source=source,
            artifact=artifact,
            payload=payload,
            lineage=lineage,
            snapshot=snapshot,
            lineage_snapshot_hash=snapshot_lineage_hash(
                snapshot.snapshot_hash,
                parent_snapshot_hashes,
                config.output_transform_version,
            ),
        )

    def _publish_prepared(
        self,
        prepared: _PreparedLocalFactorMiningArtifact,
    ) -> PublishedLocalFactorMiningArtifact:
        """Persist an already projected artifact and verify store identity exactly."""

        if type(prepared) is not _PreparedLocalFactorMiningArtifact:
            raise LocalFactorMiningArtifactBundleError(
                "prepared research artifact must be exact"
            )
        try:
            stored = self._artifact_store.put_derived(
                source=prepared.source,
                artifact=prepared.artifact,
                payload=prepared.payload,
                lineage=prepared.lineage,
            )
        except ArtifactStoreError as exc:
            raise LocalFactorMiningArtifactBundleError(
                "governed local research artifact could not be published"
            ) from exc
        if (
            stored.snapshot != prepared.snapshot
            or stored.lineage_snapshot_hash != prepared.lineage_snapshot_hash
        ):
            raise LocalFactorMiningArtifactBundleError(
                "immutable store identity differs from the deterministic research projection"
            )
        return PublishedLocalFactorMiningArtifact(
            stored=stored,
            artifact=prepared.artifact,
        )

    @staticmethod
    def _snapshot_hash(artifact: ArtifactValue) -> str:
        # The store has already revalidated the value before it reaches this adapter.
        return ArtifactSnapshot.from_artifact(cast(Artifact, artifact)).snapshot_hash


def _decode_governed_evidence_payload(
    payload: bytes,
) -> tuple[GovernedResearchArtifactKind, dict[str, object], dict[str, object], str]:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalFactorMiningArtifactBundleError(
            "research evidence payload is not JSON"
        ) from exc
    if _canonical_json_bytes(decoded) != payload:
        raise LocalFactorMiningArtifactBundleError(
            "research evidence payload must be canonical JSON"
        )
    if not isinstance(decoded, dict) or set(decoded) != {
        "content",
        "format",
        "kind",
        "research_only",
        "retention",
        "run_bundle_hash",
    }:
        raise LocalFactorMiningArtifactBundleError(
            "research evidence payload has an unsupported shape"
        )
    raw_kind = decoded["kind"]
    if (
        decoded["format"] != _GOVERNED_ARTIFACT_FORMAT
        or decoded["research_only"] is not True
        or not isinstance(raw_kind, str)
        or not isinstance(decoded["content"], dict)
        or not isinstance(decoded["run_bundle_hash"], str)
    ):
        raise LocalFactorMiningArtifactBundleError("research evidence payload is unsafe")
    try:
        kind = GovernedResearchArtifactKind(raw_kind)
        require_sha256(decoded["run_bundle_hash"], field_name="evidence.run_bundle_hash")
    except (FingerprintError, ValueError) as exc:
        raise LocalFactorMiningArtifactBundleError(
            "research evidence has an unsupported role or bundle identity"
        ) from exc
    return (
        kind,
        dict(decoded["content"]),
        _decode_evidence_retention(decoded["retention"]),
        decoded["run_bundle_hash"],
    )


def _governed_payload_format(payload: bytes) -> str:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalFactorMiningArtifactBundleError(
            "research artifact payload is not JSON"
        ) from exc
    if _canonical_json_bytes(decoded) != payload:
        raise LocalFactorMiningArtifactBundleError(
            "research artifact payload must be canonical JSON"
        )
    if not isinstance(decoded, dict) or not isinstance(decoded.get("format"), str):
        raise LocalFactorMiningArtifactBundleError(
            "research artifact payload has no recognized format"
        )
    return decoded["format"]


def _decode_evidence_retention(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "automatic_cleanup",
        "policy_id",
        "retention_days",
    }:
        raise LocalFactorMiningArtifactBundleError(
            "research evidence retention has an unsupported shape"
        )
    automatic_cleanup = value["automatic_cleanup"]
    policy_id = value["policy_id"]
    retention_days = value["retention_days"]
    if (
        automatic_cleanup is not False
        or not isinstance(policy_id, str)
        or isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or not 1 <= retention_days <= 36_500
    ):
        raise LocalFactorMiningArtifactBundleError(
            "research evidence retention values are unsafe"
        )
    return {
        "automatic_cleanup": False,
        "policy_id": policy_id,
        "retention_days": retention_days,
    }


def _nested_mapping(content: Mapping[str, object], field_name: str) -> dict[str, object]:
    value = content.get(field_name)
    if not isinstance(value, dict):
        raise LocalFactorMiningArtifactBundleError(
            f"research evidence {field_name} must be a mapping"
        )
    return value


def _assert_exact_hash(
    *,
    mapping: Mapping[str, object],
    field_name: str,
    expected: str,
) -> None:
    value = mapping.get(field_name)
    if not isinstance(value, str):
        raise LocalFactorMiningArtifactBundleError(
            f"research evidence {field_name} must be a SHA-256 hash"
        )
    try:
        observed = require_sha256(value, field_name=f"evidence.{field_name}")
    except FingerprintError as exc:
        raise LocalFactorMiningArtifactBundleError(
            f"research evidence {field_name} must be a SHA-256 hash"
        ) from exc
    if observed != expected:
        raise LocalFactorMiningArtifactBundleError(
            f"research evidence {field_name} does not match the manifest"
        )


def _decode_sealed_selection_commitment(
    *,
    selection: Mapping[str, object],
    bundle: LocalFactorMiningRunBundle,
    manifest: LocalFactorMiningRunManifest,
) -> FactorMiningSelectionCommitment:
    """Reconstruct the commitment that alone authorizes retained OOS evidence.

    This is intentionally a content check rather than a hash-label check: each
    record and the enclosing commitment rederive their hashes from the stored
    fields before their selected membership can authorize any OOS run.
    """

    expected_fields = {
        "campaign_id",
        "campaign_hash",
        "generation_receipt_hash",
        "discovery_result_hash",
        "selection_policy_hash",
        "records",
        "commitment_hash",
    }
    if set(selection) != expected_fields:
        raise LocalFactorMiningArtifactBundleError(
            "selection commitment must contain exactly its frozen typed fields"
        )
    raw_records = selection["records"]
    if not isinstance(raw_records, list):
        raise LocalFactorMiningArtifactBundleError(
            "selection commitment records must be a list"
        )
    records = tuple(
        _decode_selection_record(
            value=raw_record,
            field_name=f"selection_commitment.records[{index}]",
        )
        for index, raw_record in enumerate(raw_records)
    )
    try:
        commitment = FactorMiningSelectionCommitment(
            campaign_id=cast(str, selection["campaign_id"]),
            campaign_hash=cast(str, selection["campaign_hash"]),
            generation_receipt_hash=cast(str, selection["generation_receipt_hash"]),
            discovery_result_hash=cast(str, selection["discovery_result_hash"]),
            selection_policy_hash=cast(str, selection["selection_policy_hash"]),
            records=records,
        )
    except (FactorMiningError, TypeError, ValueError) as exc:
        raise LocalFactorMiningArtifactBundleError(
            "selection commitment cannot be reconstructed from frozen evidence"
        ) from exc
    if (
        _required_robustness_hash(
            selection["commitment_hash"],
            field_name="selection_commitment.commitment_hash",
        )
        != commitment.commitment_hash
    ):
        raise LocalFactorMiningArtifactBundleError(
            "selection commitment hash does not bind its frozen typed evidence"
        )
    if (
        commitment.campaign_id != bundle.campaign.campaign_id
        or commitment.campaign_hash != bundle.campaign.campaign_hash
        or commitment.generation_receipt_hash != bundle.generation.receipt_hash
        or commitment.discovery_result_hash != manifest.discovery_result_hash
        or commitment.selection_policy_hash != bundle.campaign.selection_policy.policy_hash
    ):
        raise LocalFactorMiningArtifactBundleError(
            "selection commitment does not bind the sealed campaign and discovery evidence"
        )
    expected_candidate_hashes = {
        item.candidate_id: item.candidate_hash for item in bundle.generation.proposals
    }
    observed_candidate_hashes = {
        item.candidate_id: item.candidate_hash for item in commitment.records
    }
    if observed_candidate_hashes != expected_candidate_hashes:
        raise LocalFactorMiningArtifactBundleError(
            "selection commitment records do not exactly bind the sealed generation receipt"
        )
    return commitment


def _decode_selection_record(
    *,
    value: object,
    field_name: str,
) -> FactorMiningSelectionRecord:
    """Decode one canonical selection record and rederive its record hash."""

    expected_fields = {
        "candidate_id",
        "candidate_hash",
        "discovery_hash",
        "disposition",
        "reason_code",
        "rank",
        "discovery_score",
        "record_hash",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} must contain exactly the frozen typed selection record fields"
        )
    try:
        record = FactorMiningSelectionRecord(
            candidate_id=cast(str, value["candidate_id"]),
            candidate_hash=cast(str, value["candidate_hash"]),
            discovery_hash=cast(str, value["discovery_hash"]),
            disposition=FactorMiningSelectionDisposition(
                cast(str, value["disposition"])
            ),
            reason_code=cast(str, value["reason_code"]),
            rank=cast(int | None, value["rank"]),
            discovery_score=cast(float | None, value["discovery_score"]),
        )
    except (FactorMiningError, TypeError, ValueError) as exc:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} cannot be reconstructed from frozen evidence"
        ) from exc
    if (
        _required_robustness_hash(value["record_hash"], field_name=f"{field_name}.record_hash")
        != record.record_hash
    ):
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} hash does not bind its frozen typed evidence"
        )
    return record


def _assert_report_robustness_bindings(
    *,
    bundle: LocalFactorMiningRunBundle,
    evidence_by_kind: Mapping[
        GovernedResearchArtifactKind, _LoadedLocalFactorMiningEvidence
    ],
    selection_commitment: FactorMiningSelectionCommitment,
) -> None:
    """Bind report conclusions to the frozen OOS run evidence exactly.

    The report is presentation evidence, not an independent research result.
    Its compact robustness rows must therefore be a canonical projection of the
    corresponding full OOS runs retained in the analyses artifact.  In
    particular, a no-OOS run must not imply a robustness conclusion.
    """

    analyses = evidence_by_kind[GovernedResearchArtifactKind.ANALYSES].content
    report = evidence_by_kind[GovernedResearchArtifactKind.REPORT].content
    raw_candidates = analyses.get("candidates")
    if not isinstance(raw_candidates, list):
        raise LocalFactorMiningArtifactBundleError(
            "analyses evidence candidates must be a list for report robustness binding"
        )

    expected_rows: list[dict[str, object]] = []
    expected_oos_bindings: dict[str, _OOSRobustnessBinding] = {}
    candidate_ids: list[str] = []
    for index, raw_candidate in enumerate(raw_candidates):
        if not isinstance(raw_candidate, dict) or set(raw_candidate) != {
            "candidate_id",
            "discovery_replay_hash",
            "oos_full_run",
        }:
            raise LocalFactorMiningArtifactBundleError(
                "analyses evidence candidate has an unsupported shape for report robustness binding"
            )
        candidate_id = _required_robustness_candidate_id(
            raw_candidate["candidate_id"],
            field_name=f"analyses.candidates[{index}].candidate_id",
        )
        candidate_ids.append(candidate_id)
        oos_full_run = raw_candidate["oos_full_run"]
        if oos_full_run is None:
            continue
        if not isinstance(oos_full_run, dict):
            raise LocalFactorMiningArtifactBundleError(
                "analyses evidence OOS run must be a mapping for report robustness binding"
            )
        robustness_row, binding = _oos_analysis_robustness_row(
            bundle=bundle,
            candidate_id=candidate_id,
            oos_full_run=oos_full_run,
            field_name=f"analyses.candidates[{index}].oos_full_run",
        )
        expected_rows.append(robustness_row)
        expected_oos_bindings[candidate_id] = binding

    if candidate_ids != sorted(candidate_ids) or len(set(candidate_ids)) != len(candidate_ids):
        raise LocalFactorMiningArtifactBundleError(
            "analyses evidence candidates must have unique canonical order for report robustness binding"
        )
    if tuple(candidate_ids) != tuple(
        sorted(item.candidate_id for item in bundle.generation.proposals)
    ):
        raise LocalFactorMiningArtifactBundleError(
            "analyses evidence candidates do not exactly bind the sealed generation receipt"
        )

    selected_candidate_ids = tuple(
        item.candidate_id for item in selection_commitment.selected_records
    )
    if tuple(expected_oos_bindings) != selected_candidate_ids:
        raise LocalFactorMiningArtifactBundleError(
            "analyses OOS evidence does not exactly match the sealed selection commitment"
        )
    report_selected_candidate_ids = report.get("selected_candidate_ids")
    if (
        not isinstance(report_selected_candidate_ids, list)
        or tuple(report_selected_candidate_ids) != selected_candidate_ids
    ):
        raise LocalFactorMiningArtifactBundleError(
            "report selected candidates do not exactly match the sealed selection commitment"
        )

    raw_report_rows = report.get("robustness")
    if not isinstance(raw_report_rows, list):
        raise LocalFactorMiningArtifactBundleError(
            "report evidence robustness must be a list"
        )
    observed_rows = tuple(
        _report_robustness_row(
            value=item,
            field_name=f"report.robustness[{index}]",
        )
        for index, item in enumerate(raw_report_rows)
    )
    expected = tuple(expected_rows)
    has_oos_evidence = GovernedResearchArtifactKind.OOS_EVIDENCE in evidence_by_kind
    if not has_oos_evidence:
        if expected:
            raise LocalFactorMiningArtifactBundleError(
                "analyses evidence cannot retain OOS robustness without OOS evidence"
            )
        if observed_rows:
            raise LocalFactorMiningArtifactBundleError(
                "report evidence robustness must be empty when OOS evidence is absent"
            )
        return
    if not expected:
        raise LocalFactorMiningArtifactBundleError(
            "OOS evidence requires frozen OOS robustness in analyses evidence"
        )
    _assert_oos_release_run_manifest_bindings(
        evidence_by_kind=evidence_by_kind,
        expected_bindings=expected_oos_bindings,
        selection_commitment=selection_commitment,
    )
    if observed_rows != expected:
        raise LocalFactorMiningArtifactBundleError(
            "report evidence robustness does not exactly bind frozen OOS analyses"
        )


def _report_robustness_row(*, value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "candidate_id",
        "passed",
        "plan_hash",
        "result_hash",
    }:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} must contain exactly the frozen robustness row fields"
        )
    return _robustness_report_row(
        candidate_id=_required_robustness_candidate_id(
            value["candidate_id"],
            field_name=f"{field_name}.candidate_id",
        ),
        robustness=value,
        field_name=field_name,
    )


def _oos_analysis_robustness_row(
    *,
    bundle: LocalFactorMiningRunBundle,
    candidate_id: str,
    oos_full_run: Mapping[str, object],
    field_name: str,
) -> tuple[dict[str, object], _OOSRobustnessBinding]:
    """Derive OOS display facts from typed proof rather than stored labels."""

    expected_fields = {
        "analyses",
        "lookahead_certificate_hash",
        "robustness",
        "robustness_proof",
        "run_manifest",
        "walk_forward",
    }
    if set(oos_full_run) != expected_fields:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} must contain one direct frozen OOS robustness proof projection"
        )
    try:
        proof = decode_factor_research_oos_robustness_proof(
            oos_full_run["robustness_proof"]
        )
    except LocalFactorMiningRunBundleError as exc:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} direct frozen OOS robustness proof is invalid"
        ) from exc
    expected_config, sealed_candidate_hash = _sealed_candidate_oos_config(
        bundle=bundle,
        candidate_id=candidate_id,
        field_name=field_name,
    )
    if proof.config != expected_config:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} direct frozen OOS robustness proof does not bind the sealed candidate config"
        )
    if (
        proof.experiment.decision_replay_plan_hash != bundle.plan.schedule_hash
        or proof.experiment.dataset_version_hashes != bundle.dataset_version_hashes
    ):
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} direct frozen OOS robustness proof does not bind the sealed replay inputs"
        )
    canonical_proof = encode_factor_research_oos_robustness_proof(proof)
    expected_projection = {
        **project_factor_research_oos_robustness_proof(proof),
        "robustness_proof": canonical_proof,
    }
    if dict(oos_full_run) != expected_projection:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} display evidence does not exactly bind the direct frozen OOS robustness proof"
        )
    alpha_factors = proof.config.alpha_factors
    if len(alpha_factors) != 1:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} direct frozen OOS robustness proof must contain one sealed alpha"
        )
    robustness = proof.robustness
    return (
        {
            "candidate_id": candidate_id,
            "passed": robustness.passed,
            "plan_hash": robustness.plan_hash,
            "result_hash": robustness.result_hash,
        },
        _OOSRobustnessBinding(
            candidate_id=candidate_id,
            candidate_hash=sealed_candidate_hash,
            factor_definition_hash=alpha_factors[0].definition_hash,
            lookahead_certificate_hash=proof.manifest.lookahead_certificate_hash,
            pipeline_config_hash=proof.config.config_hash,
            run_manifest_hash=proof.manifest.manifest_hash,
        ),
    )


def _sealed_candidate_oos_config(
    *,
    bundle: LocalFactorMiningRunBundle,
    candidate_id: str,
    field_name: str,
) -> tuple[FactorPipelineConfig, str]:
    """Reconstruct the only candidate config that the sealed receipt permits."""

    candidate = next(
        (
            item
            for item in bundle.generation.proposals
            if item.candidate_id == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} direct frozen OOS robustness proof names an unsealed candidate"
        )
    try:
        validation = validate_factor_candidate(
            campaign=bundle.campaign,
            candidate=candidate,
        )
        if (
            validation.status is not CandidateValidationStatus.VALIDATED_FOR_RESEARCH
            or validation.factor_definition is None
        ):
            raise LocalFactorMiningArtifactBundleError(
                f"{field_name} direct frozen OOS robustness proof names a rejected candidate"
            )
        return (
            bundle.campaign.template.build_config(
                campaign_id=bundle.campaign.campaign_id,
                candidate_id=candidate_id,
                factor_definition=validation.factor_definition,
            ),
            candidate.candidate_hash,
        )
    except FactorMiningError as exc:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} direct frozen OOS robustness proof cannot reconstruct the sealed candidate config"
        ) from exc


def _assert_oos_release_run_manifest_bindings(
    *,
    evidence_by_kind: Mapping[
        GovernedResearchArtifactKind, _LoadedLocalFactorMiningEvidence
    ],
    expected_bindings: Mapping[str, _OOSRobustnessBinding],
    selection_commitment: FactorMiningSelectionCommitment,
) -> None:
    """Bind every proof and its selected record to the governed OOS release."""

    oos = _nested_mapping(
        evidence_by_kind[GovernedResearchArtifactKind.OOS_EVIDENCE].content,
        "oos_release",
    )
    if (
        _required_robustness_hash(
            oos.get("selection_commitment_hash"),
            field_name="oos_release.selection_commitment_hash",
        )
        != selection_commitment.commitment_hash
    ):
        raise LocalFactorMiningArtifactBundleError(
            "OOS release does not bind the sealed selection commitment"
        )
    raw_results = oos.get("results")
    if not isinstance(raw_results, list):
        raise LocalFactorMiningArtifactBundleError(
            "OOS release results must be a list for run-manifest binding"
        )
    selected_records = {
        item.candidate_id: item for item in selection_commitment.selected_records
    }
    observed: list[_OOSRobustnessBinding] = []
    for index, raw_result in enumerate(raw_results):
        if not isinstance(raw_result, dict):
            raise LocalFactorMiningArtifactBundleError(
                "OOS release result must be a mapping for run-manifest binding"
            )
        candidate_id = _required_robustness_candidate_id(
            raw_result.get("candidate_id"),
            field_name=f"oos_release.results[{index}].candidate_id",
        )
        selected_record = selected_records.get(candidate_id)
        if selected_record is None:
            raise LocalFactorMiningArtifactBundleError(
                "OOS release names a candidate outside the sealed selection commitment"
            )
        candidate_hash = _required_robustness_hash(
            raw_result.get("candidate_hash"),
            field_name=f"oos_release.results[{index}].candidate_hash",
        )
        selection_record_hash = _required_robustness_hash(
            raw_result.get("selection_record_hash"),
            field_name=f"oos_release.results[{index}].selection_record_hash",
        )
        if (
            candidate_hash != selected_record.candidate_hash
            or selection_record_hash != selected_record.record_hash
        ):
            raise LocalFactorMiningArtifactBundleError(
                "OOS release does not bind its sealed selected candidate record"
            )
        observed.append(
            _OOSRobustnessBinding(
                candidate_id=candidate_id,
                candidate_hash=candidate_hash,
                factor_definition_hash=_required_robustness_hash(
                    raw_result.get("factor_definition_hash"),
                    field_name=f"oos_release.results[{index}].factor_definition_hash",
                ),
                lookahead_certificate_hash=_required_robustness_hash(
                    raw_result.get("lookahead_certificate_hash"),
                    field_name=f"oos_release.results[{index}].lookahead_certificate_hash",
                ),
                pipeline_config_hash=_required_robustness_hash(
                    raw_result.get("pipeline_config_hash"),
                    field_name=f"oos_release.results[{index}].pipeline_config_hash",
                ),
                run_manifest_hash=_required_robustness_hash(
                    raw_result.get("run_manifest_hash"),
                    field_name=f"oos_release.results[{index}].run_manifest_hash",
                ),
            )
        )
    expected = tuple(
        binding
        for _, binding in sorted(expected_bindings.items())
    )
    if (
        tuple(item.candidate_id for item in observed)
        != tuple(item.candidate_id for item in sorted(observed, key=lambda item: item.candidate_id))
        or len({item.candidate_id for item in observed}) != len(observed)
        or tuple(observed) != expected
    ):
        raise LocalFactorMiningArtifactBundleError(
            "OOS release does not exactly bind retained OOS run manifests"
        )


def _robustness_report_row(
    *,
    candidate_id: str,
    robustness: object,
    field_name: str,
) -> dict[str, object]:
    if not isinstance(robustness, Mapping):
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} must be a frozen robustness mapping"
        )
    passed = robustness.get("passed")
    if type(passed) is not bool:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name}.passed must be a boolean"
        )
    return {
        "candidate_id": candidate_id,
        "passed": passed,
        "plan_hash": _required_robustness_hash(
            robustness.get("plan_hash"),
            field_name=f"{field_name}.plan_hash",
        ),
        "result_hash": _required_robustness_hash(
            robustness.get("result_hash"),
            field_name=f"{field_name}.result_hash",
        ),
    }


def _required_robustness_candidate_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} must be a non-empty candidate identifier"
        )
    return value


def _required_robustness_hash(value: object, *, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise LocalFactorMiningArtifactBundleError(
            f"{field_name} must be a SHA-256 hash"
        ) from exc


def _canonical_parents(parents: tuple[ArtifactValue, ...]) -> tuple[ArtifactValue, ...]:
    canonical = tuple(sorted(parents, key=lambda item: item.content_hash))
    if len({item.content_hash for item in canonical}) != len(canonical):
        raise LocalFactorMiningArtifactBundleError(
            "governed artifact parents cannot duplicate a content hash"
        )
    return canonical


def _canonical_snapshot_hashes(
    items: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    """Return the sole publisher's parent order from immutable identities."""

    if not items:
        raise LocalFactorMiningArtifactBundleError(
            "governed artifact requires at least one immutable parent"
        )
    if any(type(snapshot) is not str or type(content) is not str for snapshot, content in items):
        raise LocalFactorMiningArtifactBundleError(
            "governed artifact parent identities are unsafe"
        )
    if len({content for _, content in items}) != len(items):
        raise LocalFactorMiningArtifactBundleError(
            "governed artifact parents cannot duplicate a content hash"
        )
    return tuple(snapshot for snapshot, _ in sorted(items, key=lambda item: item[1]))


def _safe_artifact_key(value: str) -> str:
    if not isinstance(value, str) or not value or any(
        character not in "abcdefghijklmnopqrstuvwxyz_" for character in value
    ):
        raise LocalFactorMiningArtifactBundleError("artifact key is unsupported")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalFactorMiningArtifactBundleError("research artifact content must be JSON") from exc


def _quality_rank(value: str) -> int:
    return {"pass": 0, "warn": 1, "unknown": 2, "fail": 3}[value]
