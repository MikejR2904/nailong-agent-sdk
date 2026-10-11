from pathlib import PurePosixPath, PureWindowsPath

import pytest

from nailong_agent_sdk.foundations.paths import relative_to_base, strip_extended_prefix

VOLUME = r"\\?\Volume{26a21bda-a627-11d7-9931-806e6f6e6963}\out.txt"
DEVICE = r"\\?\GLOBALROOT\Device\HarddiskVolume1\out.txt"


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        (r"\\?\C:\work\run\out.txt", r"C:\work\run\out.txt"),
        (r"\\?\c:\work", r"c:\work"),
        (r"\\?\UNC\server\share\dir\out.txt", r"\\server\share\dir\out.txt"),
        (r"\\?\unc\server\share", r"\\server\share"),
        (r"C:\work\run\out.txt", r"C:\work\run\out.txt"),
        (r"\\server\share\dir", r"\\server\share\dir"),
        (r"relative\out.txt", r"relative\out.txt"),
        (VOLUME, VOLUME),
        (DEVICE, DEVICE),
    ],
)
def test_only_drive_and_unc_extended_spellings_are_unwrapped(spelling, expected):
    assert str(strip_extended_prefix(PureWindowsPath(spelling))) == str(PureWindowsPath(expected))


def test_a_posix_path_is_never_rewritten():
    odd = PurePosixPath("\\\\?\\C:\\name")
    assert strip_extended_prefix(odd) is odd


@pytest.mark.parametrize(
    ("path", "base"),
    [
        (r"\\?\C:\work\run\shared\out.txt", r"C:\work\run"),
        (r"C:\work\run\shared\out.txt", r"\\?\C:\work\run"),
        (r"\\?\C:\work\run\shared\out.txt", r"\\?\C:\work\run"),
        (r"C:\work\run\shared\out.txt", r"C:\work\run"),
        (r"\\?\c:\WORK\run\shared\out.txt", r"C:\work\RUN"),
    ],
)
def test_the_extended_spelling_of_either_side_does_not_change_containment(path, base):
    relative = relative_to_base(PureWindowsPath(path), PureWindowsPath(base))
    assert relative == PureWindowsPath(r"shared\out.txt")


def test_the_extended_spelling_of_a_unc_path_is_contained_by_its_plain_share():
    relative = relative_to_base(
        PureWindowsPath(r"\\?\UNC\server\share\dir\out.txt"),
        PureWindowsPath(r"\\server\share"),
    )
    assert relative == PureWindowsPath(r"dir\out.txt")


@pytest.mark.parametrize(
    ("path", "base"),
    [
        (r"\\?\C:\work\other\out.txt", r"C:\work\run"),
        (r"\\?\C:\work\run2\out.txt", r"C:\work\run"),
        (r"\\?\C:\work", r"C:\work\run"),
        (r"\\?\D:\work\run\out.txt", r"C:\work\run"),
        (r"\\?\UNC\server\share\out.txt", r"C:\work\run"),
        (VOLUME, r"C:\work\run"),
    ],
)
def test_a_path_outside_the_base_is_still_refused_in_any_spelling(path, base):
    with pytest.raises(ValueError):
        relative_to_base(PureWindowsPath(path), PureWindowsPath(base))
