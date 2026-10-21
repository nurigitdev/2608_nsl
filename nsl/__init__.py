"""NeX Skill Language deterministic execution kernel."""

from ._version import __version__

from .compiler import CompilationResult, NslCompiler
from .diagnostics import (
    CompileError,
    Diagnostic,
    DiagnosticCode,
    DiagnosticPhase,
    SourceLocation,
)
from .includes import (
    IncludeEdge,
    IncludeOptions,
    IncludeResolver,
    MemoryIncludeResolver,
    SourceManifestEntry,
)
from .nsp import (
    NspBuildError,
    NspBuilder,
    NspManifest,
    NspPackage,
    NspSkillManifest,
)
from .nsp_signatures import (
    NspSignatureError,
    NspSignatureMetadata,
    NspSignatureVerifier,
    NspSigner,
)
from .nsp_verification import (
    NspVerificationCode,
    NspVerificationError,
    NspVerificationLimits,
    NspVerificationPolicy,
    NspVerifier,
    VerifiedNspPackage,
    VerifiedNspSkill,
)
from .preflight import (
    ExecutionTrustProfile,
    RuntimePreflightValidator,
    TrustPreflightError,
    TrustPreflightEvidence,
    TrustPreflightStatus,
)
from .runtime import (
    ControlledExecutionPlan,
    ExecutionRequest,
    ExecutionResult,
    RuntimeEngine,
)
from .source import SourceFile, SourceId, SourcePosition, SourceSpan
from .syntax import ParseMode
from .trust import (
    PackageTrustError,
    PackageTrustEvidence,
    TrustStatus,
    VerifiedSkillArtifact,
    package_id_for,
)
from .verification import (
    PostWriteVerificationPort,
    PostWriteVerifier,
    VerificationError,
    VerificationObservation,
    VerificationRequest,
    VerificationResult,
)

__all__ = [
    "__version__",
    "CompileError",
    "CompilationResult",
    "ControlledExecutionPlan",
    "Diagnostic",
    "DiagnosticCode",
    "DiagnosticPhase",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionTrustProfile",
    "IncludeEdge",
    "IncludeOptions",
    "IncludeResolver",
    "MemoryIncludeResolver",
    "NslCompiler",
    "NspBuildError",
    "NspBuilder",
    "NspManifest",
    "NspPackage",
    "NspSkillManifest",
    "NspSignatureError",
    "NspSignatureMetadata",
    "NspSignatureVerifier",
    "NspSigner",
    "NspVerificationCode",
    "NspVerificationError",
    "NspVerificationLimits",
    "NspVerificationPolicy",
    "NspVerifier",
    "ParseMode",
    "PackageTrustError",
    "PackageTrustEvidence",
    "PostWriteVerificationPort",
    "PostWriteVerifier",
    "RuntimeEngine",
    "RuntimePreflightValidator",
    "SourceFile",
    "SourceId",
    "SourceLocation",
    "SourceManifestEntry",
    "SourcePosition",
    "SourceSpan",
    "TrustStatus",
    "TrustPreflightError",
    "TrustPreflightEvidence",
    "TrustPreflightStatus",
    "VerifiedNspPackage",
    "VerifiedNspSkill",
    "VerifiedSkillArtifact",
    "VerificationError",
    "VerificationObservation",
    "VerificationRequest",
    "VerificationResult",
    "package_id_for",
]
