"""Standalone local composition root for governed factor-mining research runs.

This module is intentionally separate from the broad application CLI.  Its
dependency closure contains only immutable research inputs, PIT replay, factor
analysis, and artifact publishing; it does not import broker, execution,
scheduler, approval, portfolio target, or live runtime configuration code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import math

from northstar_quant.application.factor_mining_campaign import (
    FactorMiningCampaignArtifactMaterial,
    FactorMiningCampaignError,
    FactorMiningCampaignRunner,
)
from northstar_quant.application.factor_mining_tools import (
    EvaluateFactorCandidateDiscoveryBatchRequest,
)
from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.data.artifacts.immutable_store import ArtifactStore, ArtifactStoreError
from northstar_quant.data.contracts.data_domain import DerivedArtifact
from northstar_quant.research.factor_mining.artifact_bundle import (
    LoadedLocalFactorMiningRunBundle,
    LocalFactorMiningArtifactBundleError,
    LocalFactorMiningArtifactBundleStore,
    _PreparedLocalFactorMiningArtifact,
)
from northstar_quant.research.factor_mining.protocol import (
    FactorMiningDiscoveryResult,
    FactorMiningOOSRelease,
    FactorMiningSelectionCommitment,
)
from northstar_quant.research.factor_mining.run_bundle import (
    FactorResearchOOSRobustnessProof,
    GovernedResearchArtifactKind,
    GovernedResearchArtifactReference,
    LocalFactorMiningRunBundle,
    LocalFactorMiningRunManifest,
    encode_factor_research_oos_robustness_proof,
    project_factor_research_oos_robustness_proof,
)


__all__ = [
    "LocalFactorMiningDiscoverySelectionPreparation",
    "LocalFactorMiningResearchError",
    "LocalFactorMiningResearchPreparation",
    "LocalFactorMiningResearchRun",
    "LocalFactorMiningResearchService",
]


class LocalFactorMiningResearchError(RuntimeError):
    """A local factor-mining run cannot safely start, publish, or replay."""


@dataclass(frozen=True, slots=True)
class LocalFactorMiningResearchRun:
    """Published, research-only result of a sealed local factor-mining bundle."""

    bundle_snapshot_hash: str
    manifest_snapshot_hash: str
    manifest: LocalFactorMiningRunManifest
    discovery: FactorMiningDiscoveryResult
    commitment: FactorMiningSelectionCommitment
    release: FactorMiningOOSRelease | None

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    def as_mapping(self) -> dict[str, object]:
        return {
            "bundle_snapshot_hash": self.bundle_snapshot_hash,
            "manifest_hash": self.manifest.manifest_hash,
            "manifest_snapshot_hash": self.manifest_snapshot_hash,
            "research_only": True,
            "result_hash": self.manifest.result_hash,
            "selected_candidate_ids": [
                item.candidate_id for item in self.commitment.selected_records
            ],
            "oos_released": self.release is not None,
        }


@dataclass(frozen=True, slots=True)
class LocalFactorMiningResearchPreparation:
    """Hash-only, one-shot pre-publication projection of a local research run.

    The object intentionally exposes only identities and output byte count. Its
    private material remains inside the DB-free research composition root until
    :meth:`LocalFactorMiningResearchService.publish` consumes it once.  A
    process loss therefore cannot silently publish or replay a prepared run.
    """

    bundle_snapshot_hash: str
    manifest_hash: str
    result_hash: str
    discovery_result_hash: str
    selection_commitment_hash: str
    oos_release_hash: str | None
    selected_candidate_count: int
    artifact_byte_count: int
    preparation_hash: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and len(value) == 64
            for value in (
                self.bundle_snapshot_hash,
                self.manifest_hash,
                self.result_hash,
                self.discovery_result_hash,
                self.selection_commitment_hash,
                self.preparation_hash,
            )
        ):
            raise LocalFactorMiningResearchError(
                "local research preparation must expose SHA-256 identities only"
            )
        if self.oos_release_hash is not None and (
            not isinstance(self.oos_release_hash, str) or len(self.oos_release_hash) != 64
        ):
            raise LocalFactorMiningResearchError(
                "local research preparation OOS identity must be a SHA-256 or None"
            )
        if isinstance(self.selected_candidate_count, bool) or not isinstance(
            self.selected_candidate_count, int
        ) or self.selected_candidate_count < 0:
            raise LocalFactorMiningResearchError(
                "local research preparation selected candidate count is invalid"
            )
        if (self.selected_candidate_count == 0) != (self.oos_release_hash is None):
            raise LocalFactorMiningResearchError(
                "local research preparation OOS identity must match selected candidate count"
            )
        if isinstance(self.artifact_byte_count, bool) or not isinstance(
            self.artifact_byte_count, int
        ) or self.artifact_byte_count < 1:
            raise LocalFactorMiningResearchError(
                "local research preparation artifact byte count is invalid"
            )


@dataclass(frozen=True, slots=True)
class LocalFactorMiningDiscoverySelectionPreparation:
    """Hash-only discovery/selection proof retained before any OOS release.

    The private runner state remains in this DB-free service.  A durable outer
    runner may record the selection and reserve OOS before calling
    :meth:`LocalFactorMiningResearchService.prepare_release`.
    """

    bundle_snapshot_hash: str
    discovery_result_hash: str
    selection_commitment_hash: str
    selected_candidate_count: int
    preparation_hash: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and len(value) == 64
            for value in (
                self.bundle_snapshot_hash,
                self.discovery_result_hash,
                self.selection_commitment_hash,
                self.preparation_hash,
            )
        ):
            raise LocalFactorMiningResearchError(
                "local discovery/selection preparation must expose SHA-256 identities only"
            )
        if isinstance(self.selected_candidate_count, bool) or not isinstance(
            self.selected_candidate_count, int
        ) or self.selected_candidate_count < 0:
            raise LocalFactorMiningResearchError(
                "local discovery/selection selected candidate count is invalid"
            )


@dataclass(frozen=True, slots=True)
class _PreparedLocalFactorMiningResearchRun:
    """The complete, side-effect-free projection of one local research run."""

    manifest: LocalFactorMiningRunManifest
    evidence: tuple[_PreparedLocalFactorMiningArtifact, ...]
    manifest_artifact: _PreparedLocalFactorMiningArtifact


@dataclass(frozen=True, slots=True)
class _PreparedLocalFactorMiningPublication:
    """Private retained values required to publish one consumed preparation."""

    loaded: LoadedLocalFactorMiningRunBundle
    material: FactorMiningCampaignArtifactMaterial
    prepared: _PreparedLocalFactorMiningResearchRun


@dataclass(frozen=True, slots=True)
class _PreparedLocalFactorMiningDiscoverySelection:
    """One retained in-memory runner before its OOS release is permitted."""

    loaded: LoadedLocalFactorMiningRunBundle
    runner: FactorMiningCampaignRunner
    discovery: FactorMiningDiscoveryResult
    commitment: FactorMiningSelectionCommitment


class LocalFactorMiningResearchService:
    """Create, run, replay and inspect only immutable local research bundles."""

    __slots__ = (
        "_artifact_store",
        "_bundle_store",
        "_prepared_discovery_selections",
        "_prepared_publications",
    )

    def __init__(self, *, artifact_store: ArtifactStore) -> None:
        if type(artifact_store) is not ArtifactStore:
            raise LocalFactorMiningResearchError(
                "artifact_store must be an exact ArtifactStore"
            )
        self._artifact_store = artifact_store
        self._bundle_store = LocalFactorMiningArtifactBundleStore(
            artifact_store=artifact_store
        )
        self._prepared_discovery_selections: dict[
            str, _PreparedLocalFactorMiningDiscoverySelection
        ] = {}
        self._prepared_publications: dict[str, _PreparedLocalFactorMiningPublication] = {}

    def publish_definition(self, *, bundle: LocalFactorMiningRunBundle) -> str:
        """Publish a typed declaration before any CLI/API run may reference it.

        This is the explicit trusted registration seam for a human or future
        bounded AI ledger.  It accepts a concrete typed declaration, verifies
        all DatasetVersions, and returns only a hash-addressed immutable
        snapshot.  The standalone CLI itself accepts only that snapshot hash.
        """

        try:
            published = self._bundle_store.publish_definition(bundle=bundle)
        except (LocalFactorMiningArtifactBundleError, ValueError) as exc:
            raise LocalFactorMiningResearchError(
                "local factor-mining declaration could not be published"
            ) from exc
        return published.stored.snapshot.snapshot_hash

    def run(self, *, bundle_snapshot_hash: str) -> LocalFactorMiningResearchRun:
        """Run one verified declaration and publish all governed research evidence."""

        preparation = self.prepare(bundle_snapshot_hash=bundle_snapshot_hash)
        return self.publish(preparation=preparation)

    def prepare(
        self,
        *,
        bundle_snapshot_hash: str,
    ) -> LocalFactorMiningResearchPreparation:
        """Compute all immutable output identities without writing any output artifact.

        This small interface lets a durable outer runner enforce artifact-size
        and cancellation budgets before OOS evidence/report/manifest publication.
        It is deliberately not a retry cache: a preparation may be consumed at
        most once and disappears with the process.
        """

        discovery_selection = self.prepare_discovery_selection(
            bundle_snapshot_hash=bundle_snapshot_hash
        )
        return self.prepare_release(preparation=discovery_selection)

    def prepare_discovery_selection(
        self,
        *,
        bundle_snapshot_hash: str,
    ) -> LocalFactorMiningDiscoverySelectionPreparation:
        """Evaluate discovery/selection only and retain the sealed OOS gate state."""

        loaded = self._load_bundle(bundle_snapshot_hash)
        retained = self._evaluate_discovery_selection(loaded=loaded)
        selected_candidate_count = len(retained.commitment.selected_records)
        preparation_hash = canonical_json_sha256(
            {
                "bundle_snapshot_hash": loaded.stored.snapshot.snapshot_hash,
                "discovery_result_hash": retained.discovery.discovery_result_hash,
                "format": "northstar.local-factor-mining-discovery-selection-preparation.v1",
                "selected_candidate_count": selected_candidate_count,
                "selection_commitment_hash": retained.commitment.commitment_hash,
            }
        )
        result = LocalFactorMiningDiscoverySelectionPreparation(
            bundle_snapshot_hash=loaded.stored.snapshot.snapshot_hash,
            discovery_result_hash=retained.discovery.discovery_result_hash,
            selection_commitment_hash=retained.commitment.commitment_hash,
            selected_candidate_count=selected_candidate_count,
            preparation_hash=preparation_hash,
        )
        if preparation_hash in self._prepared_discovery_selections:
            raise LocalFactorMiningResearchError(
                "local discovery/selection preparation cannot be automatically replayed"
            )
        self._prepared_discovery_selections[preparation_hash] = retained
        return result

    def prepare_release(
        self,
        *,
        preparation: LocalFactorMiningDiscoverySelectionPreparation,
    ) -> LocalFactorMiningResearchPreparation:
        """Consume a retained selection and only then calculate/release OOS evidence."""

        if type(preparation) is not LocalFactorMiningDiscoverySelectionPreparation:
            raise LocalFactorMiningResearchError(
                "local research release requires an exact discovery/selection preparation"
            )
        retained = self._prepared_discovery_selections.pop(
            preparation.preparation_hash,
            None,
        )
        if retained is None:
            raise LocalFactorMiningResearchError(
                "local discovery/selection preparation is unavailable or already consumed"
            )
        if (
            retained.loaded.stored.snapshot.snapshot_hash
            != preparation.bundle_snapshot_hash
            or retained.discovery.discovery_result_hash
            != preparation.discovery_result_hash
            or retained.commitment.commitment_hash
            != preparation.selection_commitment_hash
            or len(retained.commitment.selected_records)
            != preparation.selected_candidate_count
        ):
            raise LocalFactorMiningResearchError(
                "local discovery/selection identities changed before OOS release"
            )
        material = self._release_and_collect(retained=retained)
        return self._prepare_publication(
            loaded=retained.loaded,
            material=material,
        )

    def _prepare_publication(
        self,
        *,
        loaded: LoadedLocalFactorMiningRunBundle,
        material: FactorMiningCampaignArtifactMaterial,
    ) -> LocalFactorMiningResearchPreparation:
        """Project final artifacts after the caller has opened any OOS gate."""

        prepared = self._prepare_run(loaded=loaded, material=material)
        artifact_byte_count = sum(
            len(item.payload)
            for item in (*prepared.evidence, prepared.manifest_artifact)
        )
        manifest = prepared.manifest
        preparation_hash = canonical_json_sha256(
            {
                "artifact_byte_count": artifact_byte_count,
                "bundle_snapshot_hash": loaded.stored.snapshot.snapshot_hash,
                "discovery_result_hash": material.discovery.discovery_result_hash,
                "format": "northstar.local-factor-mining-research-preparation.v1",
                "manifest_hash": manifest.manifest_hash,
                "oos_release_hash": (
                    material.release.release_hash if material.release is not None else None
                ),
                "result_hash": manifest.result_hash,
                "selected_candidate_count": len(material.commitment.selected_records),
                "selection_commitment_hash": material.commitment.commitment_hash,
            }
        )
        result = LocalFactorMiningResearchPreparation(
            bundle_snapshot_hash=loaded.stored.snapshot.snapshot_hash,
            manifest_hash=manifest.manifest_hash,
            result_hash=manifest.result_hash,
            discovery_result_hash=material.discovery.discovery_result_hash,
            selection_commitment_hash=material.commitment.commitment_hash,
            oos_release_hash=(
                material.release.release_hash if material.release is not None else None
            ),
            selected_candidate_count=len(material.commitment.selected_records),
            artifact_byte_count=artifact_byte_count,
            preparation_hash=preparation_hash,
        )
        if preparation_hash in self._prepared_publications:
            raise LocalFactorMiningResearchError(
                "local research preparation cannot be automatically replayed"
            )
        self._prepared_publications[preparation_hash] = _PreparedLocalFactorMiningPublication(
            loaded=loaded,
            material=material,
            prepared=prepared,
        )
        return result

    def publish(
        self,
        *,
        preparation: LocalFactorMiningResearchPreparation,
    ) -> LocalFactorMiningResearchRun:
        """Publish exactly one prior preparation, with no implicit recomputation."""

        if type(preparation) is not LocalFactorMiningResearchPreparation:
            raise LocalFactorMiningResearchError(
                "local research publish requires an exact prepared run"
            )
        retained = self._prepared_publications.pop(preparation.preparation_hash, None)
        if retained is None:
            raise LocalFactorMiningResearchError(
                "local research preparation is unavailable or already consumed"
            )
        expected = retained.prepared.manifest
        if (
            retained.loaded.stored.snapshot.snapshot_hash != preparation.bundle_snapshot_hash
            or expected.manifest_hash != preparation.manifest_hash
            or expected.result_hash != preparation.result_hash
            or retained.material.discovery.discovery_result_hash
            != preparation.discovery_result_hash
            or retained.material.commitment.commitment_hash
            != preparation.selection_commitment_hash
            or (
                retained.material.release.release_hash
                if retained.material.release is not None
                else None
            )
            != preparation.oos_release_hash
            or len(retained.material.commitment.selected_records)
            != preparation.selected_candidate_count
            or sum(
                len(item.payload)
                for item in (*retained.prepared.evidence, retained.prepared.manifest_artifact)
            )
            != preparation.artifact_byte_count
        ):
            raise LocalFactorMiningResearchError(
                "local research preparation identities changed before publication"
            )
        return self._publish_prepared_run(
            loaded=retained.loaded,
            material=retained.material,
            prepared=retained.prepared,
        )

    def _evaluate_loaded_bundle(
        self,
        *,
        loaded: LoadedLocalFactorMiningRunBundle,
    ) -> FactorMiningCampaignArtifactMaterial:
        """Evaluate all local stages for standalone run/replay composition only."""

        return self._release_and_collect(
            retained=self._evaluate_discovery_selection(loaded=loaded)
        )

    def _evaluate_discovery_selection(
        self,
        *,
        loaded: LoadedLocalFactorMiningRunBundle,
    ) -> _PreparedLocalFactorMiningDiscoverySelection:
        """Evaluate discovery and commit selection without invoking ``release_oos``."""

        bundle = loaded.bundle
        try:
            runner = FactorMiningCampaignRunner(
                artifact_store=self._artifact_store,
                campaign=bundle.campaign,
                plan=bundle.plan,
            )
            discovery = runner.evaluate_discovery_candidate_batch(
                request=EvaluateFactorCandidateDiscoveryBatchRequest(
                    generation=bundle.generation
                )
            )
            commitment = runner.commit_selection(discovery=discovery)
        except (FactorMiningCampaignError, ValueError) as exc:
            raise LocalFactorMiningResearchError(
                "local factor-mining discovery/selection failed closed"
            ) from exc
        return _PreparedLocalFactorMiningDiscoverySelection(
            loaded=loaded,
            runner=runner,
            discovery=discovery,
            commitment=commitment,
        )

    @staticmethod
    def _release_and_collect(
        *,
        retained: _PreparedLocalFactorMiningDiscoverySelection,
    ) -> FactorMiningCampaignArtifactMaterial:
        """Release OOS only from a retained, already committed selection."""

        try:
            release = (
                retained.runner.release_oos(commitment=retained.commitment)
                if retained.commitment.selected_records
                else None
            )
            return retained.runner.collect_research_artifact_material(
                discovery=retained.discovery,
                commitment=retained.commitment,
                release=release,
            )
        except (FactorMiningCampaignError, ValueError) as exc:
            raise LocalFactorMiningResearchError(
                "local factor-mining OOS release failed closed before evidence publication"
            ) from exc

    def replay(
        self,
        *,
        bundle_snapshot_hash: str,
        expected_manifest_snapshot_hash: str,
    ) -> LocalFactorMiningResearchRun:
        """Re-run a sealed input and require exact prior manifest/result identity."""

        try:
            expected = self._bundle_store.load_manifest(expected_manifest_snapshot_hash)
            expected_stored = self._artifact_store.load_artifact(
                expected_manifest_snapshot_hash
            )
        except (ArtifactStoreError, LocalFactorMiningArtifactBundleError) as exc:
            raise LocalFactorMiningResearchError(
                "expected local research manifest is unverified"
            ) from exc
        loaded = self._load_bundle(bundle_snapshot_hash)
        if expected.bundle_hash != loaded.bundle.bundle_hash:
            raise LocalFactorMiningResearchError(
                "expected manifest does not bind the supplied local research bundle"
            )
        material = self._evaluate_loaded_bundle(loaded=loaded)
        prepared = self._prepare_run(loaded=loaded, material=material)
        if (
            expected != prepared.manifest
            or expected_stored.snapshot != prepared.manifest_artifact.snapshot
            or expected_stored.lineage_snapshot_hash
            != prepared.manifest_artifact.lineage_snapshot_hash
        ):
            raise LocalFactorMiningResearchError(
                "local research replay does not reproduce the exact governed manifest"
            )
        return LocalFactorMiningResearchRun(
            bundle_snapshot_hash=loaded.stored.snapshot.snapshot_hash,
            manifest_snapshot_hash=expected_manifest_snapshot_hash,
            manifest=expected,
            discovery=material.discovery,
            commitment=material.commitment,
            release=material.release,
        )

    def inspect(self, *, artifact_snapshot_hash: str) -> dict[str, object]:
        """Inspect only an integrity-checked governed local research artifact."""

        try:
            return self._bundle_store.inspect(artifact_snapshot_hash)
        except LocalFactorMiningArtifactBundleError as exc:
            raise LocalFactorMiningResearchError(
                "local research inspect requires a governed immutable artifact"
            ) from exc

    def _load_bundle(self, bundle_snapshot_hash: str) -> LoadedLocalFactorMiningRunBundle:
        try:
            return self._bundle_store.load_definition(bundle_snapshot_hash)
        except LocalFactorMiningArtifactBundleError as exc:
            raise LocalFactorMiningResearchError(
                "local factor-mining run requires a verified bundle artifact"
            ) from exc

    def _prepare_run(
        self,
        *,
        loaded: LoadedLocalFactorMiningRunBundle,
        material: FactorMiningCampaignArtifactMaterial,
    ) -> _PreparedLocalFactorMiningResearchRun:
        """Project every output identity before any immutable artifact is written."""

        bundle = loaded.bundle
        common = {
            "campaign_hash": bundle.campaign.campaign_hash,
            "config_hash": bundle.config.config_hash,
            "dataset_version_hashes": list(bundle.dataset_version_hashes),
            "decision_replay_plan_hash": bundle.plan.schedule_hash,
            "generation_receipt_hash": bundle.generation.receipt_hash,
        }
        exposures = self._prepare_evidence(
            kind=GovernedResearchArtifactKind.EXPOSURES,
            loaded=loaded,
            payload={
                **common,
                "candidates": [_exposure_candidate_mapping(item) for item in material.candidates],
            },
        )
        weights = self._prepare_evidence(
            kind=GovernedResearchArtifactKind.WEIGHTS,
            loaded=loaded,
            payload={
                **common,
                "candidates": [_weight_candidate_mapping(item) for item in material.candidates],
            },
            upstream=(exposures.artifact,),
        )
        analyses = self._prepare_evidence(
            kind=GovernedResearchArtifactKind.ANALYSES,
            loaded=loaded,
            payload={
                **common,
                "candidates": [_analysis_candidate_mapping(item) for item in material.candidates],
                "discovery_stage_evidence": _plain(material.discovery),
            },
            upstream=(exposures.artifact, weights.artifact),
        )
        discovery = self._prepare_evidence(
            kind=GovernedResearchArtifactKind.DISCOVERY_EVIDENCE,
            loaded=loaded,
            payload={**common, "discovery": _plain(material.discovery)},
            upstream=(analyses.artifact,),
        )
        selection = self._prepare_evidence(
            kind=GovernedResearchArtifactKind.SELECTION_EVIDENCE,
            loaded=loaded,
            payload={**common, "selection_commitment": _plain(material.commitment)},
            upstream=(discovery.artifact,),
        )
        prepared_oos: _PreparedLocalFactorMiningArtifact | None = None
        if material.release is not None:
            prepared_oos = self._prepare_evidence(
                kind=GovernedResearchArtifactKind.OOS_EVIDENCE,
                loaded=loaded,
                payload={**common, "oos_release": _plain(material.release)},
                upstream=(selection.artifact,),
            )
        report_upstream = (
            (selection.artifact, prepared_oos.artifact)
            if prepared_oos is not None
            else (selection.artifact,)
        )
        report = self._prepare_evidence(
            kind=GovernedResearchArtifactKind.REPORT,
            loaded=loaded,
            payload={
                **common,
                "discovery_result_hash": material.discovery.discovery_result_hash,
                "limitations": [
                    "research_only",
                    "continuous_daily_series_not_actual_contract_execution",
                    "no_strategy_or_trading_admission",
                ],
                "robustness": [
                    {
                        "candidate_id": item.candidate_id,
                        "passed": item.oos_run.robustness.passed,
                        "plan_hash": item.oos_run.robustness.plan_hash,
                        "result_hash": item.oos_run.robustness.result_hash,
                    }
                    for item in material.candidates
                    if item.oos_run is not None
                ],
                "oos_release_hash": (
                    material.release.release_hash if material.release is not None else None
                ),
                "selected_candidate_ids": [
                    item.candidate_id for item in material.commitment.selected_records
                ],
                "selection_commitment_hash": material.commitment.commitment_hash,
            },
            upstream=report_upstream,
        )
        prepared_by_kind = {
            GovernedResearchArtifactKind.EXPOSURES: exposures,
            GovernedResearchArtifactKind.WEIGHTS: weights,
            GovernedResearchArtifactKind.ANALYSES: analyses,
            GovernedResearchArtifactKind.DISCOVERY_EVIDENCE: discovery,
            GovernedResearchArtifactKind.SELECTION_EVIDENCE: selection,
            GovernedResearchArtifactKind.REPORT: report,
        }
        if prepared_oos is not None:
            prepared_by_kind[GovernedResearchArtifactKind.OOS_EVIDENCE] = prepared_oos
        artifact_references = tuple(
            sorted(
                (
                    _artifact_reference(kind=kind, prepared=item)
                    for kind, item in prepared_by_kind.items()
                ),
                key=lambda item: item.kind.value,
            )
        )
        manifest = LocalFactorMiningRunManifest(
            bundle_hash=bundle.bundle_hash,
            dataset_version_hashes=bundle.dataset_version_hashes,
            decision_replay_plan_hash=bundle.plan.schedule_hash,
            campaign_hash=bundle.campaign.campaign_hash,
            generation_receipt_hash=bundle.generation.receipt_hash,
            discovery_result_hash=material.discovery.discovery_result_hash,
            selection_commitment_hash=material.commitment.commitment_hash,
            oos_release_hash=(
                material.release.release_hash if material.release is not None else None
            ),
            config_hash=bundle.config.config_hash,
            artifacts=artifact_references,
        )
        try:
            manifest_artifact = self._bundle_store._prepare_manifest(
                bundle=bundle,
                bundle_artifact=loaded.artifact,
                bundle_source=loaded.stored.source,
                manifest=manifest,
                report_artifact=report.artifact,
            )
        except LocalFactorMiningArtifactBundleError as exc:
            raise LocalFactorMiningResearchError(
                "local research evidence could not be projected into a final manifest"
            ) from exc
        evidence: tuple[_PreparedLocalFactorMiningArtifact, ...] = (
            exposures,
            weights,
            analyses,
            discovery,
            selection,
        )
        if prepared_oos is not None:
            evidence += (prepared_oos,)
        evidence += (report,)
        return _PreparedLocalFactorMiningResearchRun(
            manifest=manifest,
            evidence=evidence,
            manifest_artifact=manifest_artifact,
        )

    def _publish_prepared_run(
        self,
        *,
        loaded: LoadedLocalFactorMiningRunBundle,
        material: FactorMiningCampaignArtifactMaterial,
        prepared: _PreparedLocalFactorMiningResearchRun,
    ) -> LocalFactorMiningResearchRun:
        """Append an already verified complete projection in lineage order."""

        try:
            for evidence in prepared.evidence:
                self._bundle_store._publish_prepared(evidence)
            manifest_artifact = self._bundle_store._publish_prepared(
                prepared.manifest_artifact
            )
        except LocalFactorMiningArtifactBundleError as exc:
            raise LocalFactorMiningResearchError(
                "local research projection could not be published immutably"
            ) from exc
        if (
            manifest_artifact.stored.snapshot
            != prepared.manifest_artifact.snapshot
            or manifest_artifact.stored.lineage_snapshot_hash
            != prepared.manifest_artifact.lineage_snapshot_hash
        ):
            raise LocalFactorMiningResearchError(
                "published local research manifest differs from its deterministic projection"
            )
        return LocalFactorMiningResearchRun(
            bundle_snapshot_hash=loaded.stored.snapshot.snapshot_hash,
            manifest_snapshot_hash=manifest_artifact.stored.snapshot.snapshot_hash,
            manifest=prepared.manifest,
            discovery=material.discovery,
            commitment=material.commitment,
            release=material.release,
        )

    def _prepare_evidence(
        self,
        *,
        kind: GovernedResearchArtifactKind,
        loaded: LoadedLocalFactorMiningRunBundle,
        payload: Mapping[str, object],
        upstream: tuple[DerivedArtifact, ...] = (),
    ) -> _PreparedLocalFactorMiningArtifact:
        try:
            return self._bundle_store._prepare_evidence(
                kind=kind,
                bundle=loaded.bundle,
                bundle_artifact=loaded.artifact,
                bundle_source=loaded.stored.source,
                payload=payload,
                upstream=upstream,
            )
        except LocalFactorMiningArtifactBundleError as exc:
            raise LocalFactorMiningResearchError(
                f"governed {kind.value} research artifact could not be projected"
            ) from exc


def _artifact_reference(
    *,
    kind: GovernedResearchArtifactKind,
    prepared: _PreparedLocalFactorMiningArtifact,
) -> GovernedResearchArtifactReference:
    return GovernedResearchArtifactReference(
        kind=kind,
        snapshot_hash=prepared.snapshot.snapshot_hash,
        content_hash=prepared.snapshot.content_hash,
        lineage_snapshot_hash=prepared.lineage_snapshot_hash,
    )


def _exposure_candidate_mapping(item: object) -> dict[str, object]:
    candidate = _require_candidate_material(item)
    return {
        "candidate_id": candidate.candidate_id,
        "discovery": [
            {
                "checkpoint_data_hash": checkpoint.checkpoint_data_hash,
                "exposures": _plain(checkpoint.exposures),
            }
            for checkpoint in candidate.discovery_replay.checkpoint_data
        ],
        "oos_full_run": (
            [
                {
                    "checkpoint_data_hash": checkpoint.checkpoint_data_hash,
                    "exposures": _plain(checkpoint.exposures),
                }
                for checkpoint in candidate.oos_run.checkpoint_data
            ]
            if candidate.oos_run is not None
            else None
        ),
    }


def _weight_candidate_mapping(item: object) -> dict[str, object]:
    candidate = _require_candidate_material(item)
    return {
        "candidate_id": candidate.candidate_id,
        "discovery": _plain(candidate.discovery_replay.proposals),
        "oos_full_run": _plain(candidate.oos_run.proposals) if candidate.oos_run is not None else None,
    }


def _analysis_candidate_mapping(item: object) -> dict[str, object]:
    candidate = _require_candidate_material(item)
    if candidate.oos_run is None:
        return {
            "candidate_id": candidate.candidate_id,
            "discovery_replay_hash": candidate.discovery_replay.replay_hash,
            "oos_full_run": None,
        }
    oos_run = candidate.oos_run
    proof = FactorResearchOOSRobustnessProof(
        config=oos_run.config,
        experiment=oos_run.experiment,
        analyses=oos_run.analyses,
        robustness=oos_run.robustness,
        walk_forward=oos_run.walk_forward,
        manifest=oos_run.manifest,
    )
    return {
        "candidate_id": candidate.candidate_id,
        "discovery_replay_hash": candidate.discovery_replay.replay_hash,
        "oos_full_run": {
            **project_factor_research_oos_robustness_proof(proof),
            "robustness_proof": encode_factor_research_oos_robustness_proof(proof),
        },
    }


def _require_candidate_material(item: object):
    """Keep the serializer tolerant of a private application-only concrete class."""

    from northstar_quant.application.factor_mining_campaign import (
        FactorMiningCandidateArtifactMaterial,
    )

    if type(item) is not FactorMiningCandidateArtifactMaterial:
        raise LocalFactorMiningResearchError("artifact material contains an unsupported candidate")
    return item


def _plain(value: object) -> object:
    """Canonical JSON-safe view of typed research evidence, without runtime objects."""

    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise LocalFactorMiningResearchError("research evidence contains a non-finite number")
        return numeric
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise LocalFactorMiningResearchError("research evidence contains a naive datetime")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _plain(getattr(value, item.name))
            for item in fields(value)
        }
    raise LocalFactorMiningResearchError("research evidence includes an unsupported runtime value")
