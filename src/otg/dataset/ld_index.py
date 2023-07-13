"""Step to dump a filtered version of a LD matrix (block matrix) as Parquet files."""
from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from typing import TYPE_CHECKING

import hail as hl
import pyspark.sql.functions as f
from hail.linalg import BlockMatrix
from pyspark.sql import Window

from otg.common.schemas import parse_spark_schema
from otg.common.utils import _liftover_loci, convert_gnomad_position_to_ensembl
from otg.dataset.dataset import Dataset

if TYPE_CHECKING:
    from pyspark.sql import DataFrame
    from pyspark.sql.types import StructType


@dataclass
class LDIndex(Dataset):
    """Dataset that defines a set of variants correlated by LD across populations.

    The information comes from LD matrices made available by GnomAD.
    """

    _schema: StructType = parse_spark_schema("ld_index.json")

    @staticmethod
    def _convert_ld_matrix_to_table(
        block_matrix: BlockMatrix, min_r2: float
    ) -> DataFrame:
        """Convert LD matrix to table."""
        table = block_matrix.entries(keyed=False)
        return LDIndex._transpose_ld_matrix(
            table.filter(hl.abs(table.entry) >= min_r2**0.5)
            .to_spark()
            .withColumnRenamed("entry", "r")
        )

    @staticmethod
    def _transpose_ld_matrix(ld_matrix: DataFrame) -> DataFrame:
        """Transpose LD matrix to a square matrix format.

        # TODO: add doctest

        Args:
            ld_matrix (DataFrame): Triangular LD matrix converted to a Spark DataFrame

        Returns:
            DataFrame: Square LD matrix without diagonal duplicates
        """
        ld_matrix_transposed = ld_matrix.selectExpr("i as j", "j as i", "r")
        return ld_matrix.filter(f.col("i") == f.col("j")).unionByName(
            ld_matrix_transposed
        )

    @staticmethod
    def _process_variant_indices(
        ld_index_raw: hl.Table, grch37_to_grch38_chain_path: str
    ) -> DataFrame:
        """Creates a look up table between variants and their coordinates in the LD Matrix.

        !!! info "Gnomad's LD Matrix and Index are based on GRCh37 coordinates. This function will lift over the coordinates to GRCh38 to build the lookup table."

        Args:
            ld_index_raw (hl.Table): LD index table from GnomAD
            grch37_to_grch38_chain_path (str): Path to the chain file used to lift over the coordinates

        Returns:
            DataFrame: Look up table between variants in build hg38 and their coordinates in the LD Matrix
        """
        ld_index_38 = _liftover_loci(
            ld_index_raw, grch37_to_grch38_chain_path, "GRCh38"
        )

        return (
            ld_index_38.to_spark()
            # Filter out variants where the liftover failed
            .filter(f.col("`locus_GRCh38.position`").isNotNull())
            .withColumn(
                "chromosome", f.regexp_replace("`locus_GRCh38.contig`", "chr", "")
            )
            .withColumn(
                "position",
                convert_gnomad_position_to_ensembl(
                    f.col("`locus_GRCh38.position`"),
                    f.col("`alleles`").getItem(0),
                    f.col("`alleles`").getItem(1),
                ),
            )
            .select(
                f.concat_ws(
                    "_",
                    f.col("chromosome"),
                    f.col("position"),
                    f.col("`alleles`").getItem(0),
                    f.col("`alleles`").getItem(1),
                ).alias("variantId"),
                "position",
                f.col("idx"),
            )
            # Filter out ambiguous liftover results: multiple indices for the same variant
            .withColumn("count", f.count("*").over(Window.partitionBy(["variantId"])))
            .filter(f.col("count") == 1)
            .drop("count")
            # .repartition(400, "chromosome") # TODO: confirm this is unnecessary
            .sortWithinPartitions("position")
            .persist()
        )

    @staticmethod
    def _resolve_variant_indices(
        ld_index: DataFrame, ld_matrix: DataFrame
    ) -> DataFrame:
        """Resolve the `i` and `j` indices of the block matrix to variant IDs (build 38)."""
        ld_index_i = ld_index.selectExpr("idx as i", "variantId as variantId_i")
        ld_index_j = ld_index.selectExpr("idx as j", "variantId as variantId_j")
        # TODO: remove "j" and "i" - keeping for debugging purposes
        return ld_matrix.join(ld_index_i, on="i", how="left").join(
            ld_index_j, on="j", how="inner"
        )

    @staticmethod
    def _create_ldindex_for_population(
        ld_matrix_path: str,
        ld_index_raw_path: str,
        grch37_to_grch38_chain_path: str,
        min_r2: float,
    ) -> DataFrame:
        """Create LDIndex for a specific population."""
        # Prepare LD Block matrix
        ld_matrix = LDIndex._convert_ld_matrix_to_table(
            BlockMatrix.read(ld_matrix_path), min_r2
        )

        # Prepare table with variant indices
        ld_index = LDIndex._process_variant_indices(
            hl.read_table(ld_index_raw_path).naive_coalesce(400),
            grch37_to_grch38_chain_path,
        ).persist()

        return LDIndex._resolve_variant_indices(ld_index, ld_matrix)

    @staticmethod
    def _aggregate_ld_index_across_populations(
        unaggregated_ld_index: DataFrame,
    ) -> DataFrame:
        """Aggregate LDIndex across populations."""
        # TODO
        return unaggregated_ld_index

    @classmethod
    def from_gnomad(
        cls: type[LDIndex],
        ld_populations: list[str],
        ld_matrix_template: str,
        ld_index_raw_template: str,
        grch37_to_grch38_chain_path: str,
        min_r2: float,
    ) -> LDIndex:
        """Create LDIndex dataset aggregating the LD information across a set of populations."""
        ld_indices = []
        for pop in ld_populations:
            try:
                ld_matrix_path = ld_matrix_template.format(pop)
                ld_index_raw_path = ld_index_raw_template.format(pop)
                pop_ld_index = cls._create_ldindex_for_population(
                    ld_matrix_path,
                    ld_index_raw_path.format(pop),
                    grch37_to_grch38_chain_path,
                    min_r2,
                )
                ld_indices.append(pop_ld_index)
            except Exception as e:
                print(f"Failed to create LDIndex for population {pop}: {e}")
        ld_index_unaggregated = reduce(lambda x, y: x.unionByName(y), ld_indices)
        return cls(
            _df=cls._aggregate_ld_index_across_populations(ld_index_unaggregated),
        )
