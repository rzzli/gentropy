"""Parser for OTPlatform locus to gene gold standards curation."""

from __future__ import annotations

from typing import Type

import pyspark.sql.functions as f
from pyspark.sql import DataFrame

from gentropy.dataset.l2g_gold_standard import L2GGoldStandard
from gentropy.dataset.study_locus import StudyLocus
from gentropy.dataset.v2g import V2G


class OpenTargetsL2GGoldStandard:
    """Parser for OTGenetics locus to gene gold standards curation.

    The curation is processed to generate a dataset with 2 labels:
        - Gold Standard Positive (GSP): When the lead variant is part of a curated list of GWAS loci with known gene-trait associations.
        - Gold Standard Negative (GSN): When the lead variant is not part of a curated list of GWAS loci with known gene-trait associations but is in the vicinity of a gene's TSS.
    """

    LOCUS_TO_GENE_WINDOW = 500_000

    @classmethod
    def parse_positive_curation(
        cls: Type[OpenTargetsL2GGoldStandard], gold_standard_curation: DataFrame
    ) -> DataFrame:
        """Parse positive set from gold standard curation.

        Args:
            gold_standard_curation (DataFrame): Gold standard curation dataframe

        Returns:
            DataFrame: Positive set
        """
        return (
            gold_standard_curation.filter(
                f.col("gold_standard_info.highest_confidence").isin(["High", "Medium"])
            )
            .select(
                f.col("association_info.otg_id").alias("studyId"),
                f.col("gold_standard_info.gene_id").alias("geneId"),
                f.concat_ws(
                    "_",
                    f.col("sentinel_variant.locus_GRCh38.chromosome"),
                    f.col("sentinel_variant.locus_GRCh38.position"),
                    f.col("sentinel_variant.alleles.reference"),
                    f.col("sentinel_variant.alleles.alternative"),
                ).alias("variantId"),
                f.col("metadata.set_label").alias("source"),
            )
            .withColumn(
                "studyLocusId",
                StudyLocus.assign_study_locus_id(f.col("studyId"), f.col("variantId")),
            )
            .groupBy("studyLocusId", "studyId", "variantId", "geneId")
            .agg(f.collect_set("source").alias("sources"))
        )

    @classmethod
    def expand_gold_standard_with_negatives(
        cls: Type[OpenTargetsL2GGoldStandard], positive_set: DataFrame, v2g: V2G
    ) -> DataFrame:
        """Create full set of positive and negative evidence of locus to gene associations.

        Negative evidence consists of all genes within a window of 500kb of the lead variant that are not in the positive set.

        Args:
            positive_set (DataFrame): Positive set from curation
            v2g (V2G): Variant to gene dataset to bring distance between a variant and a gene's TSS

        Returns:
            DataFrame: Full set of positive and negative evidence of locus to gene associations
        """
        return (
            positive_set.withColumnRenamed("geneId", "curated_geneId")
            .join(
                v2g.df.selectExpr(
                    "variantId", "geneId as non_curated_geneId", "distance"
                ).filter(f.col("distance") <= cls.LOCUS_TO_GENE_WINDOW),
                on="variantId",
                how="left",
            )
            .withColumn(
                "goldStandardSet",
                f.when(
                    (f.col("curated_geneId") == f.col("non_curated_geneId"))
                    # to keep the positives that are outside the v2g dataset
                    | (f.col("non_curated_geneId").isNull()),
                    f.lit(L2GGoldStandard.GS_POSITIVE_LABEL),
                ).otherwise(L2GGoldStandard.GS_NEGATIVE_LABEL),
            )
            .withColumn(
                "geneId",
                f.when(
                    f.col("goldStandardSet") == L2GGoldStandard.GS_POSITIVE_LABEL,
                    f.col("curated_geneId"),
                ).otherwise(f.col("non_curated_geneId")),
            )
            .drop("distance", "curated_geneId", "non_curated_geneId")
        )

    @classmethod
    def as_l2g_gold_standard(
        cls: type[OpenTargetsL2GGoldStandard],
        gold_standard_curation: DataFrame,
        v2g: V2G,
    ) -> L2GGoldStandard:
        """Initialise L2GGoldStandard from source dataset.

        Args:
            gold_standard_curation (DataFrame): Gold standard curation dataframe, extracted from https://github.com/opentargets/genetics-gold-standards
            v2g (V2G): Variant to gene dataset to bring distance between a variant and a gene's TSS

        Returns:
            L2GGoldStandard: L2G Gold Standard dataset. False negatives have not yet been removed.
        """
        return L2GGoldStandard(
            _df=cls.parse_positive_curation(gold_standard_curation).transform(
                cls.expand_gold_standard_with_negatives, v2g
            ),
            _schema=L2GGoldStandard.get_schema(),
        )
