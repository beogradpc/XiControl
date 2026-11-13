"""Tests for: the map of Xiaomi robot vacuums (decrypting, reading, drawing, fetching) and homes ("families"):
which home a device is in, choosing a home on the home screen, editing homes and rooms."""
import atexit
import base64
import json
import os
import sys
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.test_fixes import _session  # noqa: E402
from xc import vacuum_map as vm  # noqa: E402
from xc.cloud import XiaomiCloud  # noqa: E402
from xc.spec import MiotSpec  # noqa: E402

app = QApplication.instance() or QApplication([])


def free_windows():
    for w in QApplication.topLevelWidgets():
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


atexit.register(free_windows)

MODEL, DID = "xiaomi.vacuum.ov81gl", "1174213502"


def _map():
    """A 6 x 4 map: walls around, room 3 on the left, floor without a room on the right, a door."""
    w, h = 6, 4
    rows_bottom_up = [
        [1, 1, 1, 1, 1, 1],
        [1, 3, 3, 255, 2, 1],
        [1, 3, 3, 2, 2, 1],
        [1, 1, 1, 1, 1, 1],
    ]
    cells = bytes(v for row in rows_bottom_up for v in row)
    return {"map_id": 1, "width": w, "height": h, "resolution": 50, "origin_x": -100, "origin_y": -200,
            "have_pile": True, "pile_x": 125, "pile_y": -125, "pile_yaw": 0,
            "map_data": base64.b64encode(zlib.compress(cells)).decode(),
            "map_room_info": [{"grid_id": 3, "room_id": 3, "color": 2}],
            "room_attrs": [{"id": 3, "room_name": "Kitchen", "name_pos_x": -25, "name_pos_y": -125}],
            "position": {"x": 25, "y": -75, "yaw": 9000},
            "paths": {"pose_id": 1, "points": json.dumps([{"x": -25, "y": -125}, {"x": 25, "y": -75}])},
            "fb_regions": [{"id": 0, "fb_point": [-100, -200, 0, -200, 0, -100, -100, -100]}]}


# -- the vacuum map -----------------------------------------------------------------------------------------
def test_map_files_are_decrypted_like_the_xiaomi_home_plugin_does():
    m = _map()
    raw = vm.encode_map(m, MODEL, DID)
    wrapped = json.loads(raw)
    assert wrapped["version"] == 2 and len(base64.b64decode(wrapped["data"])) % 16 == 0
    assert vm.decode_map(raw, MODEL, DID) == m
    # the key depends on the model's last 16 characters and the device id
    assert vm.map_key(MODEL, DID) != vm.map_key(MODEL, "1174213503")
    assert vm.map_key("xiaomi.vacuum.ov81gl", DID) == vm.map_key("aaaa" + "mi.vacuum.ov81gl", DID)
    try:
        vm.decode_map(raw, MODEL, "999")
        raise AssertionError("expected MapError")
    except vm.MapError:
        pass
    # version 1 (no encryption) and the empty file of a vacuum without a map
    assert vm.decode_map(base64.b64encode(zlib.compress(json.dumps(m).encode())), MODEL, DID)["width"] == 6
    for empty in (b"This is a null file", b""):
        try:
            vm.decode_map(empty, MODEL, DID)
            raise AssertionError("expected MapError")
        except vm.MapError as exc:
            assert "hasn't made a map" in str(exc)


def test_map_helpers_place_everything_right():
    m = _map()
    cells = vm.grid(m)
    assert len(cells) == 24 and cells[6 + 1] == 3          # second row from the bottom, second column
    # (x, y) in mm -> (column, row from the TOP of the picture)
    assert vm.to_cell(m, -100, -200) == (0.0, 4.0)        # origin: bottom-left corner
    assert vm.to_cell(m, 200, 0) == (6.0, 0.0)            # top-right corner
    assert [(p["x"], p["y"]) for p in vm.path_points(m)] == [(-25, -125), (25, -75)]
    assert vm.rooms(m) == [{"id": 3, "name": "Kitchen", "x": -25, "y": -125}]
    assert vm.room_colors(m) == {3: 2}
    unnamed = dict(m, room_attrs=[{"id": 3, "room_name": ""}])
    assert vm.rooms(unnamed)[0]["name"] == "Room 1"


def test_map_is_drawn_with_rooms_walls_and_floor():
    from gui import theme
    from gui.vacuum_map import MapView, _palette, render_grid
    theme.apply(app, "light")
    m = _map()
    img = render_grid(m)
    pal = _palette()
    assert (img.width(), img.height()) == (6, 4)
    # the vacuum stores the bottom row first: picture row 3 is grid row 0 (all walls)
    assert img.pixel(2, 3) == pal["wall"].rgba() and img.pixel(1, 2) == pal["rooms"][1].rgba()   # colour 2
    assert img.pixel(4, 2) == pal["floor"].rgba() and img.pixel(3, 2) == pal["door"].rgba()
    view = MapView()
    view.resize(400, 320)
    view.set_map(m)
    shot = view.grab().toImage()                           # paints without errors, incl. path, dock, robot
    assert shot.width() == 400 and view.message == ""


def test_map_is_fetched_through_the_mi_cloud():
    import http.server
    import threading
    raw = vm.encode_map(_map(), MODEL, DID)

    class Files(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = raw if self.path == "/map" else b"Object Not Found"
            self.send_response(200 if self.path == "/map" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Files)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    spec = MiotSpec({"type": "urn:miot-spec-v2:device:vacuum:0000A006:xiaomi-ov81gl:1", "services": [
        {"iid": 10, "type": "urn:miot-spec-v2:service:vacuum-map:0000:x:1", "description": "Vacuum Map", "properties": [
            {"iid": 1, "type": "urn:miot-spec-v2:property:map-obj-name:0000:x:1", "description": "Map Obj Name",
             "format": "string", "access": ["read", "notify"]}], "actions": []}]})
    assert vm.supports_map(spec) and not vm.supports_map(MiotSpec({"type": "x", "services": []}))

    class Cloud:
        logged_in = True
        asked = []

        def __init__(self, obj, path):
            self.obj, self.path = obj, path

        def miot_get_many(self, params):
            self.asked.append(params)
            return [{**params[0], "code": 0, "value": json.dumps({"index": 1, "obj_name": self.obj})}]

        def api(self, path, data, **kw):
            self.asked.append((path, data))
            return {"result": {"url": base + self.path, "obj_name": data["obj_name"]}}
    try:
        cloud = Cloud("1/2/0", "/map")
        m = vm.fetch_map(cloud, DID, MODEL, spec)
        assert m["width"] == 6 and cloud.asked[0] == [{"did": DID, "siid": 10, "piid": 1}]
        assert cloud.asked[1] == ("v2/home/get_interim_file_url_pro", {"obj_name": "1/2/0"})
        for c, words in ((Cloud("1/2/0", "/missing"), "hasn't uploaded"), (Cloud("", "/map"), "hasn't made a map")):
            try:
                vm.fetch_map(c, DID, MODEL, spec)
                raise AssertionError("expected MapError")
            except vm.MapError as exc:
                assert words in str(exc), str(exc)
        try:
            vm.fetch_map(None, DID, MODEL, spec)
            raise AssertionError("expected MapError")
        except vm.MapError as exc:
            assert "Sign in" in str(exc)
    finally:
        server.shutdown()
        server.server_close()


# -- homes ---------------------------------------------------------------------------------------------------------
def test_home_and_room_requests():
    c = XiaomiCloud("de", _session())
    sent = []

    def api(path, data, **kw):
        sent.append((path, data))
        if path == "home/device_list":
            return {"result": {"list": [{"did": "1", "name": "Lamp", "model": "x.light.y", "uid": c.user_id},
                                        {"did": "2", "name": "Heater", "model": "x.heater.y", "uid": c.user_id}]}}
        if path == "v2/homeroom/gethome":
            return {"result": {"homelist": [
                {"id": 11, "name": "Home", "uid": 99, "dids": ["1"], "icon": "h1", "latitude": "44.8",
                 "longitude": "20.4", "city_id": "123", "roomlist": [{"id": 21, "name": "Kitchen", "dids": []}]},
                {"id": 12, "name": "Work", "uid": 99, "dids": [], "roomlist": [{"id": 22, "name": "Office",
                                                                               "dids": ["2"]}]}]}}
        if path == "homeroom/addroom":
            return {"result": {"id": 23}}
        return {"result": {}}
    c.api = api
    devices = {d["did"]: d for d in c.get_devices()}
    assert (devices["1"]["home_id"], devices["1"]["home"], devices["1"]["room"]) == ("11", "Home", "")
    assert (devices["2"]["home_id"], devices["2"]["home"], devices["2"]["room"]) == ("12", "Work", "Office")
    homes = c.homes()
    home = homes[0]
    assert home["raw"] == {"icon": "h1", "latitude": "44.8", "longitude": "20.4", "city_id": "123"}
    c.rename_home(home, "Kuća")
    assert sent[-1] == ("homeroom/ctrhome", {"id": "11", "type": "set", "home_owner": 99, "app_ver": 7, "name": "Kuća",
                                              "icon": "h1", "city_id": "123", "latitude": 44.8, "longitude": 20.4})
    assert c.add_room(home, "Garage") == "23"
    assert sent[-1] == ("homeroom/addroom", {"type": "addroom", "name": "Garage", "home_owner": 99, "parent_id": "11",
                                             "background": "", "did": []})
    c.rename_room(home, home["rooms"][0], "Dining room")
    assert sent[-1] == ("homeroom/ctrroom", {"type": "set", "home_owner": 99, "id": "21", "name": "Dining room",
                                             "app_ver": 7})
    c.delete_room(home, home["rooms"][0])
    assert sent[-1] == ("homeroom/ctrroom", {"type": "delete", "id": "21", "app_ver": 7, "home_owner": 99})


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
