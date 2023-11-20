"""Step to apply PICS finemapping."""

from __future__ import annotations

from dataclasses import dataclass

from omegaconf import MISSING

from otg.common.session import Session
from otg.dataset.study_locus import StudyLocus
from otg.method.pics import PICS


@dataclass
class PICSStep:
    """PICS finemapping of LD-annotated StudyLocus.

    Attributes:
        session (Session): Session object.

        study_locus_ld_annotated_in (str): Path to Study Locus with the LD information annotated
        study_index_path (str): Path to study index.
    """

    session: Session = MISSING
    study_locus_ld_annotated_in: str = MISSING
    picsed_study_locus_out: str = MISSING

    def __post_init__(self: PICSStep) -> None:
        """Run step."""
        # Extract
        study_locus_ld_annotated = StudyLocus.from_parquet(
            self.session, self.study_locus_ld_annotated_in
        )
        # PICS
        picsed_sl = PICS.finemap(study_locus_ld_annotated).annotate_credible_sets()
        # Write
        picsed_sl.df.write.mode(self.session.write_mode).parquet(
            self.picsed_study_locus_out
        )
