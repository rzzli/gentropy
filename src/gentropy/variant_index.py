"""Step to generate variant index dataset."""

from __future__ import annotations

from gentropy.common.session import Session
from gentropy.config import VariantIndexConfig
from gentropy.dataset.variant_index import VariantIndex
from gentropy.datasource.ensembl.vep_parser import VariantEffectPredictorParser
from gentropy.datasource.open_targets.variants import OpenTargetsVariant


class VariantIndexStep:
    """Generate variant index based on a VEP output in json format.

    The variant index is a dataset that contains variant annotations extracted from VEP output. It is expected that all variants in the VEP output are present in the variant index.
    There's an option to provide extra variant annotations to be added to the variant index eg. allele frequencies from GnomAD.
    """

    def __init__(
        self: VariantIndexStep,
        session: Session,
        vep_output_json_path: str,
        variant_index_path: str,
        hash_threshold: int = VariantIndexConfig().hash_threshold,
        gnomad_variant_annotations_path: str | None = None,
    ) -> None:
        """Run VariantIndex step.

        Args:
            session (Session): Session object.
            vep_output_json_path (str): Variant effect predictor output path (in json format).
            variant_index_path (str): Variant index dataset path to save resulting data.
            hash_threshold (int): Hash threshold for variant identifier lenght.
            gnomad_variant_annotations_path (str | None): Path to extra variant annotation dataset.
        """
        # Extract variant annotations from VEP output:
        variant_index = VariantEffectPredictorParser.extract_variant_index_from_vep(
            session.spark, vep_output_json_path, hash_threshold
        )

        # Process variant annotations if provided:
        if gnomad_variant_annotations_path:
            # Read variant annotations from parquet:
            annotations = VariantIndex.from_parquet(
                session=session,
                path=gnomad_variant_annotations_path,
                recursiveFileLookup=True,
            )

            # Update file with extra annotations:
            variant_index = variant_index.add_annotation(annotations)

        (
            variant_index.df.withColumn("variantId", VariantIndex.assign_variant_id())
            .write.partitionBy("chromosome")
            .mode(session.write_mode)
            .parquet(variant_index_path)
        )


class ConvertToVcfStep:
    """Convert dataset with variant annotation to VCF step."""

    def __init__(
        self,
        session: Session,
        source_path: str,
        source_format: str,
        vcf_path: str,
    ) -> None:
        """Initialize step.

        Args:
            session (Session): Session object.
            source_path (str): Input dataset path.
            source_format(str): Format of the input dataset.
            vcf_path (str): Output VCF file path.
        """
        # Load
        df = session.load_data(source_path, source_format).limit(100)
        # Extract
        vcf_df = OpenTargetsVariant.as_vcf_df(session, df)
        # Write
        vcf_df.toPandas().to_csv(vcf_path, sep="\t", index=False)
