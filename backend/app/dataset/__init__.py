"""Dataset package: synthetic benchmark generator + public-data provenance.

This package is deliberately **independent of the production decision engine**.
It never imports ``backend.app.services.verifier`` or ``backend.app.services.analysis``.
Ground truth is authored per case by the generator; the production pipeline must
infer the same conclusions from the generated PDFs alone.
"""

__version__ = "1.0.0"
DATASET_NAME = "procurement-benchmark"
DATASET_COHORT = "synthetic-v1"