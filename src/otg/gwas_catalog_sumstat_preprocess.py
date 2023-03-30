"""Step to generate variant annotation dataset."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyspark.sql import SparkSession

from otg.config import GWASCatalogSumstatsPreprocessConfig
from otg.dataset.summary_statistics import SummaryStatistics

if TYPE_CHECKING:
    from otg.common.session import Session


@dataclass
class GWASCatalogSumstatsPreprocessStep(GWASCatalogSumstatsPreprocessConfig):
    """Step to preprocess GWAS Catalog harmonised summary stats."""

    session: Session = SparkSession.builder.getOrCreate()

    def run(self: GWASCatalogSumstatsPreprocessConfig) -> None:
        """Run Step."""
        # Extract
        self.session.logger.info(self.raw_sumstats_path)
        self.session.logger.info(self.out_sumstats_path)
        self.session.logger.info(self.study_id)

        # Reading dataset:
        raw_dataset = self.session.spark.read.csv(
            self.raw_sumstats_path, header=True, sep="\t"
        )
        self.session.logger.info(
            f"Number of single point associations: {raw_dataset.count()}"
        )

        # Processing dataset:
        SummaryStatistics.from_gwas_harmonized_summary_stats(
            raw_dataset, self.study_id
        ).df.write.mode(self.session.write_mode).parquet(self.out_sumstats_path)
        self.session.logger.info("Processing dataset successfully completed.")
