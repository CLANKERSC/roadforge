from __future__ import annotations

from pathlib import Path

from forge_worker import licences
from forge_worker.cache import DownloadCache
from forge_worker.selection import SelectionRequest, build_selection
from forge_worker.sources.copernicus import dem_tile_name, dem_tiles_for
from forge_worker.sources.cuzk import CuzkDmr5g, grid_tiles
from forge_worker.sources.osm import cells_for
from forge_worker.sources.registry import ALL_SOURCES, REQUIRED_LAYERS, choose

from .test_geo_selection import square

PRAGUE = build_selection(SelectionRequest(mode="area", polygon=square(14.44, 50.08, 2.0)))
QUEENSTOWN = build_selection(SelectionRequest(mode="area", polygon=square(168.66, -45.03, 2.0)))


def test_every_adapter_has_licence_and_attribution() -> None:
    assert len(ALL_SOURCES) >= 5
    for src in ALL_SOURCES:
        assert licences.problems(src.info) == [], src.info.id
    assert len({s.info.id for s in ALL_SOURCES}) == len(ALL_SOURCES)


def test_credits_list_every_source_used() -> None:
    infos = [s.info for s in ALL_SOURCES]
    text = licences.credits_text(infos, 2026)
    for info in infos:
        assert info.attribution.format(year=2026) in text
    assert "not for navigation" in text
    assert "{year}" not in text


def test_czech_area_prefers_national_data_with_global_fallback() -> None:
    chosen = choose(PRAGUE)
    assert [s.info.id for s in chosen["elevation"]] == ["cuzk_dmr5g", "copernicus_dem_glo30"]
    assert [s.info.id for s in chosen["imagery"]] == ["cuzk_ortofoto", "sentinel2_l2a"]
    assert [s.info.id for s in chosen["vectors"]] == ["osm_overpass"]


def test_anywhere_in_the_world_has_all_layers() -> None:
    spots = [
        (168.66, -45.03),
        (-122.42, 37.77),
        (139.69, 35.68),
        (-21.94, 64.15),
        (18.42, -33.92),
        (10.45, 46.53),
    ]
    for lon, lat in spots:
        sel = build_selection(SelectionRequest(mode="area", polygon=square(lon, lat, 1.0)))
        chosen = choose(sel)
        for layer in REQUIRED_LAYERS:
            assert chosen[layer], f"no {layer} source at {lat}, {lon}"
        assert chosen["elevation"][0].info.id == "copernicus_dem_glo30"


def test_tile_grids() -> None:
    assert dem_tile_name(50, 14) == "Copernicus_DSM_COG_10_N50_00_E014_00_DEM"
    assert dem_tile_name(-46, -169) == "Copernicus_DSM_COG_10_S46_00_W169_00_DEM"
    assert dem_tiles_for(PRAGUE) == [(50, 14)]
    assert dem_tiles_for(QUEENSTOWN) == [(-46, 168)]
    assert cells_for(PRAGUE) == [(144, 500)]
    tiles = grid_tiles(PRAGUE, CuzkDmr5g.TILE_M)
    assert 1 <= len(tiles) <= 6
    # Prague in S-JTSK is around x=-743 km, y=-1043 km
    assert all(-380 < ix < -350 and -520 < iy < -500 for ix, iy in tiles)


def test_cache_reuses_and_survives_failed_downloads(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path / "cache")
    calls = []

    def fetch(tmp: Path) -> None:
        calls.append(1)
        tmp.write_bytes(b"hello")

    first = cache.get_or_fetch("src", "a/b:c", ".bin", fetch)
    second = cache.get_or_fetch("src", "a/b:c", ".bin", fetch)
    assert not first.from_cache and second.from_cache and len(calls) == 1
    assert second.path.read_bytes() == b"hello" and second.sha256 == first.sha256

    def broken(tmp: Path) -> None:
        tmp.write_bytes(b"half")
        raise OSError("connection lost")

    try:
        cache.get_or_fetch("src", "bad", ".bin", broken)
    except OSError:
        pass
    assert cache.lookup("src", "bad") is None
    assert not list((tmp_path / "cache" / "src").glob("bad*"))  # no half file left behind
    second.path.write_bytes(b"corrupted!")  # size changed -> treated as missing
    assert cache.lookup("src", "a/b:c") is None
    cache.close()
