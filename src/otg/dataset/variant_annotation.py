"""Variant annotation dataset."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import pyspark.sql.functions as f

from otg.common.schemas import parse_spark_schema
from otg.common.spark_helpers import get_record_with_maximum_value, normalise_column
from otg.dataset.dataset import Dataset
from otg.dataset.v2g import V2G

if TYPE_CHECKING:
    from pyspark.sql import Column, DataFrame
    from pyspark.sql.types import StructType

    from otg.dataset.gene_index import GeneIndex


@dataclass
class VariantAnnotation(Dataset):
    """Dataset with variant-level annotations."""

    @classmethod
    def get_schema(cls: type[VariantAnnotation]) -> StructType:
        """Provides the schema for the VariantAnnotation dataset."""
        return parse_spark_schema("variant_annotation.json")

    def max_maf(self: VariantAnnotation) -> Column:
        """Maximum minor allele frequency accross all populations.

        Returns:
            Column: Maximum minor allele frequency accross all populations.
        """
        return f.array_max(
            f.transform(
                self.df.alleleFrequencies,
                lambda af: f.when(af.alleleFrequency > 0.5, 1 - af.alleleFrequency).otherwise(af.alleleFrequency),
            )
        )

    def filter_by_variant_df(self: VariantAnnotation, df: DataFrame, cols: list[str]) -> VariantAnnotation:
        """Filter variant annotation dataset by a variant dataframe.

        Args:
            df (DataFrame): A dataframe of variants
            cols (List[str]): A list of columns to join on

        Returns:
            VariantAnnotation: A filtered variant annotation dataset
        """
        self.df = self._df.join(f.broadcast(df.select(cols)), on=cols, how="inner")
        return self

    def get_transcript_consequence_df(self: VariantAnnotation, filter_by: Optional[GeneIndex] = None) -> DataFrame:
        """Dataframe of exploded transcript consequences.

        Optionally the trancript consequences can be reduced to the universe of a gene index.

        Args:
            filter_by (GeneIndex): A gene index. Defaults to None.

        Returns:
            DataFrame: A dataframe exploded by transcript consequences with the columns variantId, chromosome, transcriptConsequence
        """
        # exploding the array removes records without VEP annotation
        transript_consequences = self.df.withColumn(
            "transcriptConsequence", f.explode("vep.transcriptConsequences")
        ).select(
            "variantId",
            "chromosome",
            "position",
            "transcriptConsequence",
            f.col("transcriptConsequence.geneId").alias("geneId"),
        )
        if filter_by:
            transript_consequences = transript_consequences.join(
                f.broadcast(filter_by.df),
                on=["chromosome", "geneId"],
            )
        return transript_consequences.persist()

    def get_most_severe_vep_v2g(
        self: VariantAnnotation,
        vep_consequences: DataFrame,
        filter_by: GeneIndex,
    ) -> V2G:
        """Creates a dataset with variant to gene assignments based on VEP's predicted consequence on the transcript.

        Optionally the trancript consequences can be reduced to the universe of a gene index.

        Args:
            vep_consequences (DataFrame): A dataframe of VEP consequences
            filter_by (GeneIndex): A gene index to filter by. Defaults to None.

        Returns:
            V2G: High and medium severity variant to gene assignments
        """
        vep_lut = vep_consequences.select(
            f.element_at(f.split("Accession", r"/"), -1).alias("variantFunctionalConsequenceId"),
            f.col("Term").alias("label"),
            f.col("v2g_score").cast("double").alias("score"),
        )

        return V2G(
            _df=self.get_transcript_consequence_df(filter_by).select(
                "variantId",
                "chromosome",
                "position",
                f.col("transcriptConsequence.geneId").alias("geneId"),
                f.explode("transcriptConsequence.consequenceTerms").alias("label"),
                f.lit("vep").alias("datatypeId"),
                f.lit("variantConsequence").alias("datasourceId"),
            )
            # A variant can have multiple predicted consequences on a transcript, the most severe one is selected
            .join(
                f.broadcast(vep_lut),
                on="label",
                how="inner",
            )
            .filter(f.col("score") != 0)
            .transform(lambda df: get_record_with_maximum_value(df, ["variantId", "geneId"], "score")),
            _schema=V2G.get_schema(),
        )

    def get_polyphen_v2g(self: VariantAnnotation, filter_by: Optional[GeneIndex] = None) -> V2G:
        """Creates a dataset with variant to gene assignments with a PolyPhen's predicted score on the transcript.

        Polyphen informs about the probability that a substitution is damaging. Optionally the trancript consequences can be reduced to the universe of a gene index.

        Args:
            filter_by (GeneIndex): A gene index to filter by. Defaults to None.

        Returns:
            V2G: variant to gene assignments with their polyphen scores
        """
        return V2G(
            _df=(
                self.get_transcript_consequence_df(filter_by)
                .filter(f.col("transcriptConsequence.polyphenScore").isNotNull())
                .select(
                    "variantId",
                    "chromosome",
                    "position",
                    "geneId",
                    f.col("transcriptConsequence.polyphenScore").alias("score"),
                    f.col("transcriptConsequence.polyphenPrediction").alias("label"),
                    f.lit("vep").alias("datatypeId"),
                    f.lit("polyphen").alias("datasourceId"),
                )
            ),
            _schema=V2G.get_schema(),
        )

    def get_sift_v2g(self: VariantAnnotation, filter_by: GeneIndex) -> V2G:
        """Creates a dataset with variant to gene assignments with a SIFT's predicted score on the transcript.

        SIFT informs about the probability that a substitution is tolerated so scores nearer zero are more likely to be deleterious.
        Optionally the trancript consequences can be reduced to the universe of a gene index.

        Args:
            filter_by (GeneIndex): A gene index to filter by.

        Returns:
            V2G: variant to gene assignments with their SIFT scores
        """
        return V2G(
            _df=(
                self.get_transcript_consequence_df(filter_by)
                .filter(f.col("transcriptConsequence.siftScore").isNotNull())
                .select(
                    "variantId",
                    "chromosome",
                    "position",
                    "geneId",
                    f.expr("1 - transcriptConsequence.siftScore").alias("score"),
                    f.col("transcriptConsequence.siftPrediction").alias("label"),
                    f.lit("vep").alias("datatypeId"),
                    f.lit("sift").alias("datasourceId"),
                )
            ),
            _schema=V2G.get_schema(),
        )

    def get_plof_v2g(self: VariantAnnotation, filter_by: GeneIndex) -> V2G:
        """Creates a dataset with variant to gene assignments with a flag indicating if the variant is predicted to be a loss-of-function variant by the LOFTEE algorithm.

        Optionally the trancript consequences can be reduced to the universe of a gene index.

        Args:
            filter_by (GeneIndex): A gene index to filter by.

        Returns:
            V2G: variant to gene assignments from the LOFTEE algorithm
        """
        return V2G(
            _df=(
                self.get_transcript_consequence_df(filter_by)
                .filter(f.col("transcriptConsequence.lof").isNotNull())
                .withColumn(
                    "isHighQualityPlof",
                    f.when(f.col("transcriptConsequence.lof") == "HC", True).when(
                        f.col("transcriptConsequence.lof") == "LC", False
                    ),
                )
                .withColumn(
                    "score",
                    f.when(f.col("isHighQualityPlof"), 1.0).when(~f.col("isHighQualityPlof"), 0),
                )
                .select(
                    "variantId",
                    "chromosome",
                    "position",
                    "geneId",
                    "isHighQualityPlof",
                    f.col("score"),
                    f.lit("vep").alias("datatypeId"),
                    f.lit("loftee").alias("datasourceId"),
                )
            ),
            _schema=V2G.get_schema(),
        )

    def get_distance_to_tss(
        self: VariantAnnotation,
        filter_by: GeneIndex,
        max_distance: int = 500_000,
    ) -> V2G:
        """Extracts variant to gene assignments for variants falling within a window of a gene's TSS.

        Args:
            filter_by (GeneIndex): A gene index to filter by.
            max_distance (int): The maximum distance from the TSS to consider. Defaults to 500_000.

        Returns:
            V2G: variant to gene assignments with their distance to the TSS
        """
        return V2G(
            _df=(
                self.df.alias("variant")
                .join(
                    f.broadcast(filter_by.locations_lut()).alias("gene"),
                    on=[
                        f.col("variant.chromosome") == f.col("gene.chromosome"),
                        f.abs(f.col("variant.position") - f.col("gene.tss")) <= max_distance,
                    ],
                    how="inner",
                )
                .withColumn(
                    "inverse_distance",
                    max_distance - f.abs(f.col("variant.position") - f.col("gene.tss")),
                )
                .transform(lambda df: normalise_column(df, "inverse_distance", "score"))
                .select(
                    "variantId",
                    f.col("variant.chromosome").alias("chromosome"),
                    "position",
                    "geneId",
                    "score",
                    f.lit("distance").alias("datatypeId"),
                    f.lit("canonical_tss").alias("datasourceId"),
                )
            ),
            _schema=V2G.get_schema(),
        )
