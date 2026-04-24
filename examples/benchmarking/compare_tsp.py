# /// script
# dependencies = [
#   "httpx",
#   "folium",
# ]
# ///

import os

import folium
import httpx
from folium.plugins import DualMap, AntPath
from folium.features import DivIcon

# Configuration - set OSRM_API_URL env var to point to your host
API_BASE_URL = os.environ.get("OSRM_API_URL", "http://10.211.55.28:8080")

# Warehouse location (Start and End)
WAREHOUSE = {"longitude": -84.05157, "latitude": 9.93971}  # Montes de Oca

# Delivery Stops (scrambled order)
STOPS = [
    {"longitude": -84.0516238, "latitude": 9.9397177}, {"longitude": -84.0516242, "latitude": 9.9397206},
         {"longitude": -84.0516218, "latitude": 9.9397094}, {"longitude": -84.0577468, "latitude": 9.937693},
         {"longitude": -84.0578645, "latitude": 9.9376648}, {"longitude": -84.0577556, "latitude": 9.9376979},
         {"longitude": -84.0694619, "latitude": 9.9438103}, {"longitude": -84.0694904, "latitude": 9.9438243},
         {"longitude": -84.0694879, "latitude": 9.9438245}, {"longitude": -84.0694879, "latitude": 9.9438245},
         {"longitude": -84.0694668, "latitude": 9.9438126}, {"longitude": -84.0694187, "latitude": 9.9437528},
         {"longitude": -84.0694703, "latitude": 9.943818}, {"longitude": -84.0694101, "latitude": 9.9437453},
         {"longitude": -84.0760184, "latitude": 9.9418387}, {"longitude": -84.0759812, "latitude": 9.9418969},
         {"longitude": -84.0760094, "latitude": 9.9418995}, {"longitude": -84.0759893, "latitude": 9.9418879},
         {"longitude": -84.0760245, "latitude": 9.9418656}, {"longitude": -84.0760154, "latitude": 9.9418262},
         {"longitude": -84.0760154, "latitude": 9.9418262}, {"longitude": -84.0747138, "latitude": 9.9422161},
         {"longitude": -84.0760945, "latitude": 9.9418018}, {"longitude": -84.0760945, "latitude": 9.9418018},
         {"longitude": -84.0708026, "latitude": 9.948101}, {"longitude": -84.0707804, "latitude": 9.9481154},
         {"longitude": -84.071458, "latitude": 9.9481305}, {"longitude": -84.071458, "latitude": 9.9481305},
         {"longitude": -84.0714702, "latitude": 9.948057}, {"longitude": -84.0711025, "latitude": 9.9481826},
         {"longitude": -84.0713888, "latitude": 9.9483127}, {"longitude": -84.0713881, "latitude": 9.9483143},
         {"longitude": -84.0713404, "latitude": 9.9482662}, {"longitude": -84.0651347, "latitude": 9.9528968},
         {"longitude": -84.0659558, "latitude": 9.9531388}, {"longitude": -84.0654087, "latitude": 9.9534248},
         {"longitude": -84.0644352, "latitude": 9.9536793}, {"longitude": -84.0643048, "latitude": 9.9536506},
         {"longitude": -84.0641475, "latitude": 9.9536137}, {"longitude": -84.0644443, "latitude": 9.949931},
         {"longitude": -84.06264, "latitude": 9.9512348}, {"longitude": -84.0619851, "latitude": 9.9541326},
         {"longitude": -84.0602576, "latitude": 9.9422669}, {"longitude": -84.0589126, "latitude": 9.9432122},
         {"longitude": -84.058913, "latitude": 9.943188}, {"longitude": -84.0590324, "latitude": 9.9431494},
         {"longitude": -84.0588269, "latitude": 9.943476}, {"longitude": -84.0588338, "latitude": 9.9434969},
         {"longitude": -84.0588626, "latitude": 9.9434244}, {"longitude": -84.0588634, "latitude": 9.9434588},
         {"longitude": -84.0488518, "latitude": 9.9490353}, {"longitude": -84.048788, "latitude": 9.9489952},
         {"longitude": -84.0488624, "latitude": 9.9490737}, {"longitude": -84.0487859, "latitude": 9.94896},
         {"longitude": -84.0488808, "latitude": 9.9490915}, {"longitude": -84.0488339, "latitude": 9.9490534},
         {"longitude": -84.04765, "latitude": 9.9500195}, {"longitude": -84.0476685, "latitude": 9.9500259},
         {"longitude": -84.0476198, "latitude": 9.9499793}, {"longitude": -84.047648, "latitude": 9.9500156},
         {"longitude": -84.0476338, "latitude": 9.9499544}, {"longitude": -84.0476612, "latitude": 9.949946},
         {"longitude": -84.0476147, "latitude": 9.9499265}, {"longitude": -84.0476612, "latitude": 9.9499957},
         {"longitude": -84.0478674, "latitude": 9.9498057}, {"longitude": -84.0478736, "latitude": 9.9498199},
         {"longitude": -84.0483789, "latitude": 9.9534755}, {"longitude": -84.0484169, "latitude": 9.9534221},
         {"longitude": -84.0483836, "latitude": 9.9534666}, {"longitude": -84.0483799, "latitude": 9.9534513},
         {"longitude": -84.0483799, "latitude": 9.9534513}, {"longitude": -84.0483797, "latitude": 9.9534808},
         {"longitude": -84.0483901, "latitude": 9.9533792}, {"longitude": -84.0483101, "latitude": 9.9527117},
         {"longitude": -84.0377991, "latitude": 9.9568614}, {"longitude": -84.0378157, "latitude": 9.9568607},
         {"longitude": -84.037717, "latitude": 9.9534805}, {"longitude": -84.0376875, "latitude": 9.9535338},
         {"longitude": -84.0377991, "latitude": 9.9535161}, {"longitude": -84.03776, "latitude": 9.9534684},
         {"longitude": -84.0378056, "latitude": 9.9535004}, {"longitude": -84.0377768, "latitude": 9.9535651},
         {"longitude": -84.0351134, "latitude": 9.9533922}, {"longitude": -84.0347868, "latitude": 9.953508},
         {"longitude": -84.0351606, "latitude": 9.953354}, {"longitude": -84.0350909, "latitude": 9.9531947},
         {"longitude": -84.0351567, "latitude": 9.9532905}, {"longitude": -84.0117468, "latitude": 9.9539064},
         {"longitude": -84.0117467, "latitude": 9.953727}, {"longitude": -84.0112493, "latitude": 9.9537305},
         {"longitude": -84.0103493, "latitude": 9.9536462}, {"longitude": -83.9951736, "latitude": 9.9575882},
         {"longitude": -83.9950457, "latitude": 9.9575783}, {"longitude": -83.9951043, "latitude": 9.9575178},
         {"longitude": -83.9951227, "latitude": 9.9576431}, {"longitude": -83.9918731, "latitude": 9.9598449},
         {"longitude": -83.9920135, "latitude": 9.9598242}, {"longitude": -83.9919052, "latitude": 9.9598654},
         {"longitude": -83.9919052, "latitude": 9.9598654}, {"longitude": -83.9919583, "latitude": 9.9599488},
         {"longitude": -83.9919735, "latitude": 9.959778}, {"longitude": -83.991976, "latitude": 9.959825},
         {"longitude": -83.9919775, "latitude": 9.9598136}, {"longitude": -83.9948977, "latitude": 9.9614356},
         {"longitude": -83.9948935, "latitude": 9.961458}, {"longitude": -84.0396758, "latitude": 9.9570083},
         {"longitude": -84.0396707, "latitude": 9.9570078}, {"longitude": -84.0399792, "latitude": 9.9572547},
         {"longitude": -84.0399098, "latitude": 9.9571594}, {"longitude": -84.0398273, "latitude": 9.9571533},
         {"longitude": -84.0269527, "latitude": 9.9562748}, {"longitude": -84.0268789, "latitude": 9.9575305},
         {"longitude": -84.0231334, "latitude": 9.9585394}, {"longitude": -84.0190051, "latitude": 9.9602933},
         {"longitude": -84.0189772, "latitude": 9.9602878}, {"longitude": -84.0190909, "latitude": 9.9601707},
         {"longitude": -84.0187989, "latitude": 9.9602769}, {"longitude": -84.0186726, "latitude": 9.9602709},
         {"longitude": -84.0232109, "latitude": 9.9592499}, {"longitude": -84.0231928, "latitude": 9.959246},
         {"longitude": -84.0183238, "latitude": 9.9658414}, {"longitude": -84.0183576, "latitude": 9.965912},
         {"longitude": -84.0183856, "latitude": 9.9659548}, {"longitude": -84.0183856, "latitude": 9.9659548},
         {"longitude": -84.0186075, "latitude": 9.9661076}, {"longitude": -84.0184568, "latitude": 9.9659226},
         {"longitude": -84.018624, "latitude": 9.9662115}, {"longitude": -84.0186551, "latitude": 9.9661418},
         {"longitude": -84.0131724, "latitude": 9.9678056}, {"longitude": -84.0131253, "latitude": 9.967873},
         {"longitude": -84.0131703, "latitude": 9.967813}, {"longitude": -84.0056384, "latitude": 9.964814},
         {"longitude": -84.0068552, "latitude": 9.9646434}, {"longitude": -84.0068255, "latitude": 9.96408},
         {"longitude": -84.0068392, "latitude": 9.9640863}, {"longitude": -84.0068636, "latitude": 9.9640813},
         {"longitude": -84.0068381, "latitude": 9.9640584}, {"longitude": -83.9980114, "latitude": 9.9675059},
         {"longitude": -84.0060736, "latitude": 9.9803328}, {"longitude": -84.0061306, "latitude": 9.9802454},
         {"longitude": -84.0060703, "latitude": 9.9801948}, {"longitude": -84.0168652, "latitude": 9.9733058},
         {"longitude": -84.0168601, "latitude": 9.9732635}, {"longitude": -84.0169272, "latitude": 9.9731972},
         {"longitude": -84.040078, "latitude": 9.9568048}, {"longitude": -84.0495564, "latitude": 9.9484962},
         {"longitude": -84.0496811, "latitude": 9.9487425}, {"longitude": -84.0496815, "latitude": 9.9486487},
         {"longitude": -84.0714341, "latitude": 9.9480369}, {"longitude": -84.0713992, "latitude": 9.9481214},
         {"longitude": -84.0713942, "latitude": 9.9480743}, {"longitude": -84.0713973, "latitude": 9.9480729},
         {"longitude": -84.0779816, "latitude": 9.9466571}, {"longitude": -84.0781986, "latitude": 9.9472903},
         {"longitude": -84.0781498, "latitude": 9.947168}, {"longitude": -84.0782057, "latitude": 9.947289},
         {"longitude": -84.0462112, "latitude": 9.951582}, {"longitude": -84.0395624, "latitude": 9.9548919},
         {"longitude": -84.0395735, "latitude": 9.9548575}, {"longitude": -84.0395327, "latitude": 9.9547834},
         {"longitude": -84.0395353, "latitude": 9.9547884}, {"longitude": -84.0395825, "latitude": 9.9548496},
         {"longitude": -84.0395825, "latitude": 9.9548496}, {"longitude": -84.0657036, "latitude": 9.9656935},
         {"longitude": -84.0657609, "latitude": 9.9657801}, {"longitude": -84.0656959, "latitude": 9.965746},
         {"longitude": -84.0657443, "latitude": 9.9657392}, {"longitude": -84.0657443, "latitude": 9.9657392}
]
# STOPS = [
#    {"longitude": -84.0500, "latitude": 9.9333},  # San Pedro
#    {"longitude": -84.0750, "latitude": 9.9167},  # Plaza Viquez
#    {"longitude": -84.0833, "latitude": 9.9333},  # Central Park
#    {"longitude": -84.0667, "latitude": 9.9500},  # Tournon
# ]

# Combined list for processing
COORDINATES = [WAREHOUSE] + STOPS


def get_route(coords):
    print(f"Requesting actual round-trip route (sequence as provided)...")
    # For a round trip in /route, the destination MUST be the origin
    payload = {
        "origin": coords[0],
        "destination": coords[0],  # Return to Warehouse
        "waypoints": coords[1:],  # All stops are intermediate waypoints
        "alternatives": False
    }
    response = httpx.post(f"{API_BASE_URL}/route", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def get_trip(coords, roundtrip=True):
    print(f"Requesting TSP optimized trip (roundtrip={roundtrip})...")
    # For /trip, roundtrip=True automatically returns to the 'source' point
    payload = {
        "coordinates": coords,
        "roundtrip": roundtrip,
        "source": "first",
        "destination": "last" if not roundtrip else "any"
    }
    response = httpx.post(f"{API_BASE_URL}/trip", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def main():
    try:
        # Configuration for comparison
        ROUNDTRIP = True  # Warehouse round trip

        # Fetch routes
        route_data = get_route(COORDINATES)
        trip_data = get_trip(COORDINATES, roundtrip=ROUNDTRIP)

        # Initialize side-by-side maps centered in San Jose
        m = DualMap(location=[9.9333, -84.0833], zoom_start=14, tiles="cartodbpositron")

        # 1. Plot "Actual" Route (Red) on Left Map (m1)
        geometry = route_data["routes"][0]["geometry"]
        distance = route_data['routes'][0]['distance'] / 1000
        duration = route_data['routes'][0]['duration'] / 60

        line_coords = [(p[1], p[0]) for p in geometry["coordinates"]]
        AntPath(
            line_coords,
            color="red",
            weight=5,
            opacity=0.7,
            delay=1000,
            tooltip=f"Actual Round Trip: {distance:.2f}km, {duration:.1f}min"
        ).add_to(m.m1)

        # 2. Plot "TSP Optimized" Trip (Green) on Right Map (m2)
        trip_geom = trip_data["trips"][0]["geometry"]
        trip_dist = trip_data['trips'][0]['distance'] / 1000
        trip_dur = trip_data['trips'][0]['duration'] / 60

        trip_line_coords = [(p[1], p[0]) for p in trip_geom["coordinates"]]
        AntPath(
            trip_line_coords,
            color="green",
            weight=5,
            opacity=0.8,
            delay=1000,
            dash_array=[10, 20],
            tooltip=f"TSP Optimized Round Trip: {trip_dist:.2f}km, {trip_dur:.1f}min"
        ).add_to(m.m2)

        # Add Title Macros to distinguish maps
        # Add Title Macros to distinguish maps with more prominent styling
        title_html_m1 = f'''
             <div style="position: fixed; top: 10px; left: 10%; width: 30%; z-index:9999; 
                         background-color: rgba(255, 75, 75, 0.8); color: white; padding: 10px; 
                         border-radius: 5px; text-align: center; font-weight: bold;">
                 ACTUAL ROUTE: {distance:.2f} km
             </div>
             '''
        title_html_m2 = f'''
             <div style="position: fixed; top: 10px; right: 10%; width: 30%; z-index:9999; 
                         background-color: rgba(46, 204, 113, 0.8); color: white; padding: 10px; 
                         border-radius: 5px; text-align: center; font-weight: bold;">
                 OPTIMIZED ROUTE: {trip_dist:.2f} km
             </div>
             '''
        m.m1.get_root().html.add_child(folium.Element(title_html_m1))
        m.m2.get_root().html.add_child(folium.Element(title_html_m2))

        # Add a shared Legend
        legend_html = f'''
             <div style="
                position: fixed; 
                bottom: 50px; left: 50%; 
                transform: translateX(-50%);
                width: 300px; height: 140px; 
                background-color: white; border:2px solid grey; z-index:9999; font-size:12px;
                padding: 10px; border-radius: 10px; opacity: 0.9;
                ">
                <b>Legend</b><br>
                <i class="fa fa-home" style="color:darkblue"></i> Warehouse (Start/End)<br>
                <span style="display:inline-block; width:12px; height:12px; background:#ff4b4b; border-radius:50%; border:1px solid white;"></span> Target Stop (Actual Map)<br>
                <span style="display:inline-block; width:12px; height:12px; background:#2ecc71; border-radius:50%; border:1px solid white;"></span> Target Stop (Optimized Map)<br>
                <span style="color:red">---</span> Actual Route (Animated)<br>
                <span style="color:green">---</span> TSP Optimized Route (Animated)<br>
             </div>
             '''
        m.get_root().html.add_child(folium.Element(legend_html))

        # Interpret the "best" sequence
        waypoints = trip_data["waypoints"]
        optimized_sequence = sorted([(w["waypoint_index"], i) for i, w in enumerate(waypoints)])

        sequence_list = []
        for pos, idx in optimized_sequence:
            if idx == 0:
                sequence_list.append("WAREHOUSE")
            else:
                sequence_list.append(f"Stop {idx}")

        if ROUNDTRIP:
            sequence_list.append("WAREHOUSE")

        sequence_str = " -> ".join(sequence_list)

        # Mark coordinates
        # Helper for numbered markers
        def get_numbered_icon(number, color="blue"):
            return DivIcon(
                icon_size=(30, 30),
                icon_anchor=(15, 15),
                html=f'''
                    <div style="
                        background-color: {color};
                        color: white;
                        border-radius: 50%;
                        width: 24px;
                        height: 24px;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        font-weight: bold;
                        font-size: 12px;
                        border: 2px solid white;
                        box-shadow: 0 0 4px rgba(0,0,0,0.4);
                    ">{number}</div>
                '''
            )

        # Mark coordinates
        # Warehouse (Start)
        folium.Marker(
            [WAREHOUSE["latitude"], WAREHOUSE["longitude"]],
            popup="WAREHOUSE (Start/End)",
            icon=folium.Icon(color="darkblue", icon="home")
        ).add_to(m)

        # Stops
        for i, stop in enumerate(STOPS):
            idx = i + 1  # Original sequence index
            opt_rank = waypoints[idx]["waypoint_index"]  # TSP optimized rank

            # Actual sequence on Left Map (m1)
            folium.Marker(
                [stop["latitude"], stop["longitude"]],
                popup=f"Stop {idx} (Original Position)",
                icon=get_numbered_icon(idx, color="#ff4b4b") # Reddish for actual
            ).add_to(m.m1)

            # Optimized sequence on Right Map (m2)
            folium.Marker(
                [stop["latitude"], stop["longitude"]],
                popup=f"Stop {idx}<br>TSP Rank: {opt_rank}",
                icon=get_numbered_icon(opt_rank, color="#2ecc71") # Greenish for optimized
            ).add_to(m.m2)

        # Save and show
        output_file = "examples/benchmarking/comparison_map.html"
        m.save(output_file)

        print(f"\n--- TSP ROUND-TRIP Comparison ---")
        print(f"Warehouse: {WAREHOUSE['longitude']}, {WAREHOUSE['latitude']}")
        print(f"Stops: {len(STOPS)}")
        print(f"\nActual Route (Red):   {distance:.2f} km | {duration:.1f} min")
        print(f"TSP Optimized (Green): {trip_dist:.2f} km | {trip_dur:.1f} min")
        print(f"\nBest (Optimized) Sequence:")
        print(sequence_str)
        print(f"\nOptimization Gain: {distance - trip_dist:.2f} km SAVED")
        print(f"\nMap saved to {os.path.abspath(output_file)}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
