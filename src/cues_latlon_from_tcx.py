"""Extract waypoints from a TCX file and write them to a CSV file..

"""
import xml.etree.ElementTree as ET
import csv
import argparse
import sys

import logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("tcx", help="Path to TCX route file")
    parser.add_argument("output", help="Path to output CSV file")
    args = parser.parse_args()

    # Open gpx file
    try:
        if args.tcx == "-":
            args.f_tcx = sys.stdin
        else:
            args.f_tcx = open(args.tcx, 'r', encoding="utf-8-sig")
    except Exception as e:
        print(f"Error opening TCX file: {e}", file=sys.stderr)
        sys.exit(1)


    # Open output file
    try:
        if args.output == "-":
            args.f_output = sys.stdout
        else:
            args.f_output = open(args.output, 'w', encoding="utf-8-sig")
    except Exception as e:
        print(f"Error opening output file: {e}", file=sys.stderr)
        sys.exit(1)

    return args


def load_tcx_course_points(f) -> list[dict]:
    result = []
    try:
        # Parse the TCX XML file
        tree = ET.parse(f)
        root = tree.getroot()

        # Automatically detect the XML namespace (TCX files heavily rely on this)
        # ElementTree tags look like '{http://garmin.com}TrainingCenterDatabase'
        namespace = ''
        if root.tag.startswith('{'):
            namespace = root.tag.split('}')[0] + '}'

        # Define the namespace prefix dictionary for the XPath search
        ns = {'ns': namespace.strip('{}')} if namespace else {}

        # Search for all CoursePoint elements anywhere in the document
        xpath_query = './/ns:CoursePoint' if namespace else './/CoursePoint'
        course_points = root.findall(xpath_query, ns)

        assert course_points, "No <CoursePoint> elements found in this file."

        log.debug(f"Found {len(course_points)} course points\n")
        for cp in course_points:
            # Extract standard TCX child elements safely
            # Note: Ride With GPS usually populates Name, Type, and sometimes Notes
            desc = cp.findtext(f'{namespace}Notes', default='N/A')
            cp_type = cp.findtext(f'{namespace}PointType', default='N/A')
            notes = cp.findtext(f'{namespace}Notes', default='').strip()

            # Extract coordinates from the nested Position element
            lat = 'N/A'
            lon = 'N/A'
            position = cp.find(f'{namespace}Position', ns)
            if position is not None:
                lat = position.findtext(f'{namespace}LatitudeDegrees', default='N/A')
                lon = position.findtext(f'{namespace}LongitudeDegrees', default='N/A')

            result.append({"Cue": desc, "Latitude": lat, "Longitude": lon})

    except FileNotFoundError:
        print(f"Error: The file '{f: fil}' could not be found.")
    except ET.ParseError:
        print("Error: Failed to parse XML. Check if the file is a valid TCX format.")
    return result



if __name__ == "__main__":
    args = cli()
    tcx_route = load_tcx_course_points(args.f_tcx)
    writer = csv.DictWriter(args.f_output, fieldnames=tcx_route[0].keys())
    writer.writeheader()
    writer.writerows(tcx_route)
