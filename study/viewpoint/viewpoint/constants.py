"""Project-wide constants for the tweet group-misleadingness pipeline."""

SEED = 1
BW = 0.1            # repo CRH Gaussian kernel bandwidth
SOMEWHAT = 0.7      # repo GaussianParams.somewhatHelpfulValue

MISLEADING = "MISINFORMED_OR_POTENTIALLY_MISLEADING"
NOT_MISLEADING = "NOT_MISLEADING"

# Absolute paths (this box).
ROOT = "/projects/bdata/advaitmb/derad-agent/tsv_generation"
SCORING_SRC = f"{ROOT}/communitynotes/scoring/src"
CN_DATA = f"{ROOT}/cn_data"
OUT_DIR = f"{CN_DATA}/viewpoint_out"
