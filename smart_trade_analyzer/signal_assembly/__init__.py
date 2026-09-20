"""Signal Assembly -- Phase 6.

Builds the frozen SignalRecord contract out of a pipeline/ PipelineResult.
Makes no trading decision -- quality_gate/gate.py remains the sole
authority for Decision; this package only compiles its output, and the
rest of the pipeline's, into the final persisted/displayed shape.
"""
from .builder import build_signal_record, compile_reasons_and_warnings

__all__ = ["build_signal_record", "compile_reasons_and_warnings"]
