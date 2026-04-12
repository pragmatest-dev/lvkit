"""Tests for LabVIEW error handling code generation."""

import pytest

from lvkit.labview_error import LabVIEWError, merge_errors, no_error_cluster


class TestLabVIEWError:
    """Tests for LabVIEWError exception class."""

    def test_basic_creation(self) -> None:
        """Test creating a LabVIEWError."""
        error = LabVIEWError(code=42, source="MyVI.vi", message="Test error")
        assert error.code == 42
        assert error.source == "MyVI.vi"
        assert error.message == "Test error"

    def test_default_values(self) -> None:
        """Test default values."""
        error = LabVIEWError()
        assert error.code == 0
        assert error.source == ""
        assert error.message == ""

    def test_str_formatting(self) -> None:
        """Test string formatting."""
        error = LabVIEWError(code=42, source="Test.vi", message="Something went wrong")
        assert "42" in str(error)
        assert "Something went wrong" in str(error)
        assert "Test.vi" in str(error)

    def test_str_formatting_no_message(self) -> None:
        """Test string formatting without message."""
        error = LabVIEWError(code=42, source="Test.vi")
        assert "42" in str(error)
        assert "Test.vi" in str(error)

    def test_repr(self) -> None:
        """Test repr formatting."""
        error = LabVIEWError(code=42, source="Test.vi", message="Test")
        repr_str = repr(error)
        assert "LabVIEWError" in repr_str
        assert "42" in repr_str

    def test_from_cluster_with_error(self) -> None:
        """Test creating from error cluster with error."""
        cluster = {"status": True, "code": 42, "source": "Test.vi"}
        error = LabVIEWError.from_cluster(cluster)
        assert error is not None
        assert error.code == 42
        assert error.source == "Test.vi"

    def test_from_cluster_no_error(self) -> None:
        """Test creating from error cluster without error."""
        cluster = {"status": False, "code": 0, "source": ""}
        error = LabVIEWError.from_cluster(cluster)
        assert error is None

    def test_from_cluster_none(self) -> None:
        """Test creating from None cluster."""
        error = LabVIEWError.from_cluster(None)
        assert error is None

    def test_to_cluster(self) -> None:
        """Test converting to cluster dict."""
        error = LabVIEWError(code=42, source="Test.vi")
        cluster = error.to_cluster()
        assert cluster["status"] is True
        assert cluster["code"] == 42
        assert cluster["source"] == "Test.vi"

    def test_is_error(self) -> None:
        """Test is_error property."""
        assert LabVIEWError(code=-1).is_error is True
        assert LabVIEWError(code=0).is_error is False
        assert LabVIEWError(code=1).is_error is False

    def test_is_warning(self) -> None:
        """Test is_warning property."""
        assert LabVIEWError(code=1).is_warning is True
        assert LabVIEWError(code=0).is_warning is False
        assert LabVIEWError(code=-1).is_warning is False

    def test_exception_raised(self) -> None:
        """Test that LabVIEWError can be raised and caught."""
        with pytest.raises(LabVIEWError) as exc_info:
            raise LabVIEWError(code=42, source="Test.vi")
        assert exc_info.value.code == 42


class TestMergeErrors:
    """Tests for merge_errors function."""

    def test_first_error_wins(self) -> None:
        """Test first error takes priority."""
        error1 = LabVIEWError(code=1, source="First")
        error2 = LabVIEWError(code=2, source="Second")
        result = merge_errors(error1, error2)
        assert result is error1

    def test_second_if_first_none(self) -> None:
        """Test second error used if first is None."""
        error2 = LabVIEWError(code=2, source="Second")
        result = merge_errors(None, error2)
        assert result is error2

    def test_none_if_both_none(self) -> None:
        """Test None returned if both are None."""
        result = merge_errors(None, None)
        assert result is None


class TestNoErrorCluster:
    """Tests for no_error_cluster function."""

    def test_returns_no_error(self) -> None:
        """Test that no_error_cluster returns valid no-error cluster."""
        cluster = no_error_cluster()
        assert cluster["status"] is False
        assert cluster["code"] == 0
        assert cluster["source"] == ""
