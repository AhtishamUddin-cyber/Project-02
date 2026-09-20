"""End-to-End Analytical Pipeline Orchestration -- Phase 6.

Sequences the approved Phase 2-5 engines (FeatureEngine -> MarketRegime ->
Setup detection -> Confluence -> Entry -> Risk -> Quality Gate) for one
already-fetched MarketData snapshot. No I/O of its own -- see
scanner/engine.py for the entry point that fetches data and calls this
package.
"""
from .models import PipelineResult
from .orchestrator import run_pipeline

__all__ = ["PipelineResult", "run_pipeline"]
