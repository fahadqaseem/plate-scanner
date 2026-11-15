"""Register your own trucks so /recognize can say match / no-match.

  python examples/add_vehicle.py --table lpr-vehicles --plate "ABC-1234" --vehicle-id T01
"""
import argparse
import re

import boto3

ap = argparse.ArgumentParser()
ap.add_argument("--table", required=True)
ap.add_argument("--plate", required=True)
ap.add_argument("--vehicle-id", required=True)
ap.add_argument("--region", default="us-east-2")
a = ap.parse_args()

plate = re.sub(r"[^A-Z0-9]", "", a.plate.upper())
boto3.resource("dynamodb", region_name=a.region).Table(a.table).put_item(
    Item={"plate_number": plate, "vehicle_id": a.vehicle_id})
print(f"Registered {a.vehicle_id}")
