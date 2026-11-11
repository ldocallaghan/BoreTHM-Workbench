"""The compatibility module gives the public names of a BoreTHM with the
operations interface, and the same objects from BoreTHM 1.8.0's modules."""
import borethm
import numpy as np

from borethm_workbench import model_compat
from borethm_workbench.programme import interface_available


def test_each_name_is_the_public_one_where_the_model_exports_it():
    for name in model_compat.NAMES:
        value = getattr(model_compat, name)
        if interface_available():
            assert value is getattr(borethm, name), name
        else:
            assert callable(value), name


def test_the_values_agree_with_the_model():
    info = model_compat.water_table_info()
    assert set(info) == {"path", "exists", "temperatures_C", "pressures_Pa"}
    assert info["temperatures_C"][0] == 1.0 and np.all(np.diff(info["pressures_Pa"]) > 0)
    pipes = model_compat.named_pipes()
    assert "TK-Drakon coated" in pipes and all(pipe.name == name for name, pipe in pipes.items())
    assert "assumed" in model_compat.data_bases()
    # The model's year, as its exposure for one year gives it.
    assert float(model_compat.exposure_seconds(np.zeros(1), 1.0, None)[0]) == 3.1536e7
