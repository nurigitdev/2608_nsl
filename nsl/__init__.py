"""NeX Skill Language vertical-slice implementation."""

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
from .runtime import ExecutionRequest, ExecutionResult, RuntimeEngine
from .source import SourceFile, SourceId, SourcePosition, SourceSpan
from .syntax import ParseMode

__all__ = [
    "CompileError",
    "CompilationResult",
    "Diagnostic",
    "DiagnosticCode",
    "DiagnosticPhase",
    "ExecutionRequest",
    "ExecutionResult",
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
    "RuntimeEngine",
    "SourceFile",
    "SourceId",
    "SourceLocation",
    "SourceManifestEntry",
    "SourcePosition",
    "SourceSpan",
    "VerifiedNspPackage",
    "VerifiedNspSkill",
]
