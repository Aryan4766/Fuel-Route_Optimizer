from fuelroute.services.station_prep import (
    Gazetteer,
    load_unique_us_stations,
    normalise_place,
    strip_place_suffix,
)

GAZ = (
    "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG   \n"
    "OK\t1\t1\tBig Cabin town\t43\tA\t1\t1\t1.0\t0.0\t36.54\t-95.21\n"
    "TX\t2\t1\tSpringfield CDP\t57\tS\t1\t1\t90.0\t0.0\t30.00\t-98.00\n"
    "TX\t3\t1\tSpringfield city\t25\tA\t1\t1\t5.0\t0.0\t31.00\t-97.00\n"
    "MO\t4\t1\tSt. Louis city\t25\tA\t1\t1\t60.0\t0.0\t38.63\t-90.24\n"
    "TN\t5\t1\tNashville-Davidson metropolitan government (balance)\t00\tA\t1\t1\t475.0\t0.0\t36.17\t-86.78\n"
    "AL\t7\t1\tMcCalla town\t43\tA\t1\t1\t10.0\t0.0\t33.33\t-87.00\n"
    "NE\t8\t1\tO'Neill city\t25\tA\t1\t1\t3.0\t0.0\t42.46\t-98.64\n"
    "TX\t6\t1\tBroken row\t25\tA\t1\t1\tabc\t0.0\tnan\tnan\n"
)

RAW = (
    "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
    '7,WOODSHED,"I-44, EXIT 283",Big Cabin,OK,307,3.00\n'
    '20,PILOT #1243,"I-8, EXIT 119",Gila Bend,AZ,930,3.899\n'
    '20,PILOT  1243,"I-8, EXIT 119",Gila Bend,AZ,930,3.799\n'
    '66827,FLYING J,QEW,Fort Erie,ON,430,5.1\n'
    '99,BAD PRICE,x,Somewhere,TX,1,abc\n'
    '98,ZERO,x,Somewhere,TX,1,0\n'
    ',NO ID,x,Somewhere,TX,1,3.0\n'
)


def test_normalise_place():
    assert normalise_place("St. Louis") == normalise_place("Saint Louis") == "st louis"
    assert normalise_place("Mount Vernon") == "mt vernon"
    assert normalise_place("Nashville-Davidson metropolitan government (balance)") == (
        "nashville davidson metropolitan government"
    )


def test_strip_place_suffix():
    assert strip_place_suffix("abbeville city") == "abbeville"
    assert strip_place_suffix("juneau city and borough") == "juneau"
    assert strip_place_suffix("city") == "city"  # nothing left to strip safely


def test_gazetteer_exact_prefix_and_preference():
    gaz = Gazetteer.from_text(GAZ)
    place, how = gaz.lookup("Big Cabin", "OK")
    assert (how, place.lat, place.lon) == ("exact", 36.54, -95.21)
    assert gaz.lookup("Saint Louis", "MO")[0].lat == 38.63
    # incorporated city beats the much larger census-designated place of the same name
    assert gaz.lookup("Springfield", "TX")[0].lat == 31.00
    place, how = gaz.lookup("Nashville", "TN")
    assert how == "prefix" and place.lat == 36.17
    assert gaz.lookup("Mc Calla", "AL")[0].lat == 33.33  # spacing differences are ignored
    assert gaz.lookup("Oneill", "NE")[0].lat == 42.46
    assert gaz.lookup("Atlantis", "TX") is None
    assert gaz.lookup("Big Cabin", "TX") is None  # state must match
    assert gaz.lookup("", "TX") is None


def test_load_unique_us_stations():
    stations, report = load_unique_us_stations(RAW)
    assert report.rows == 7
    assert report.non_us_rows == 1
    assert report.invalid_rows == 3
    assert [s.opis_id for s in stations] == [7, 20]
    pilot = stations[1]
    assert pilot.price == 3.799  # lowest of the duplicate listings
    assert pilot.name == "PILOT 1243"  # name from the winning row, whitespace collapsed
