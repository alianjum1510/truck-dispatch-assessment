import json
import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from shapely.geometry import LineString, Point
from api.models import TruckStop
from django.shortcuts import render

def get_coordinates(location):
    url = f"https://nominatim.openstreetmap.org/search?q={location}&format=json&limit=1"
    headers = {'User-Agent': 'FuelRoutingApp/1.0'}
    response = requests.get(url, headers=headers).json()
    if response:
        return float(response[0]['lat']), float(response[0]['lon'])
    return None, None

@csrf_exempt
def compute_fuel_route(request):
    if request.method == 'POST':
        try:
            payload = json.loads(request.body)
            origin_loc = payload.get('origin')
            destination_loc = payload.get('destination')

            if not origin_loc or not destination_loc:
                return JsonResponse({'error': 'Origin and destination locations are required'}, status=400)

            origin_lat, origin_lon = get_coordinates(origin_loc)
            dest_lat, dest_lon = get_coordinates(destination_loc)

            if origin_lat is None or dest_lat is None:
                return JsonResponse({'error': 'Could not get coordinates for one or both locations'}, status=400)

            # Get route from OSRM
            routing_url = f"http://router.project-osrm.org/route/v1/driving/{origin_lon},{origin_lat};{dest_lon},{dest_lat}?overview=full&geometries=geojson"
            routing_res = requests.get(routing_url).json()

            if routing_res.get('code') != 'Ok':
                return JsonResponse({'error': 'Could not generate path'}, status=400)

            path_data = routing_res['routes'][0]
            path_geometry = path_data['geometry']
            dist_meters = path_data['distance']
            dist_miles = dist_meters * 0.000621371

            line_geom = LineString(path_geometry['coordinates'])
            
            # Find stops near route
            available_stations = TruckStop.objects.filter(lat__isnull=False, lon__isnull=False)
            potential_stations = []
            
            min_lon, min_lat, max_lon, max_lat = line_geom.bounds
            
            stations_in_bounds = available_stations.filter(
                lat__gte=min_lat - 0.1, lat__lte=max_lat + 0.1,
                lon__gte=min_lon - 0.1, lon__lte=max_lon + 0.1
            )

            for station in stations_in_bounds:
                point_geom = Point(station.lon, station.lat)
                if line_geom.distance(point_geom) < 0.05:  # ~3.5 miles
                    proj_dist = line_geom.project(point_geom, normalized=True)
                    dist_on_path = proj_dist * dist_miles
                    potential_stations.append({
                        'id': station.opis_id,
                        'name': station.name,
                        'address': station.address,
                        'city': station.city,
                        'state': station.state,
                        'price': station.retail_price,
                        'lat': station.lat,
                        'lon': station.lon,
                        'dist': dist_on_path
                    })

            potential_stations.sort(key=lambda x: x['dist'])

            # Optimal routing algorithm
            path_stations = [{'dist': 0, 'price': potential_stations[0]['price'] if potential_stations else 0, 'id': 'origin', 'name': 'Origin'}] + potential_stations
            path_stations.append({'dist': dist_miles, 'price': 0, 'id': 'destination', 'name': 'Destination'})

            overall_expense = 0
            curr_index = 0
            remaining_fuel_range = 0
            chosen_stations = []

            # Check if reachable
            is_reachable = True
            for i in range(1, len(path_stations)):
                if path_stations[i]['dist'] - path_stations[i-1]['dist'] > 500:
                    is_reachable = False
                    break

            if not is_reachable:
                return JsonResponse({'error': 'Destination is unreachable with 500 miles range (no stations found)'}, status=400)

            while curr_index < len(path_stations) - 1:
                curr_stop = path_stations[curr_index]
                next_cheaper_index = None
                
                for k in range(curr_index + 1, len(path_stations)):
                    if path_stations[k]['dist'] - curr_stop['dist'] > 500:
                        break
                    if path_stations[k]['price'] < curr_stop['price']:
                        next_cheaper_index = k
                        break

                if next_cheaper_index is not None:
                    dist_to_cover = path_stations[next_cheaper_index]['dist'] - curr_stop['dist']
                    if remaining_fuel_range < dist_to_cover:
                        gallons_req = (dist_to_cover - remaining_fuel_range) / 10.0
                        expense = gallons_req * curr_stop['price']
                        overall_expense += expense
                        remaining_fuel_range = dist_to_cover
                        if curr_stop['id'] != 'origin':
                            chosen_stations.append({
                                'station_id': curr_stop['id'],
                                'name': curr_stop['name'],
                                'lat': curr_stop.get('lat'),
                                'lon': curr_stop.get('lon'),
                                'gallons': round(gallons_req, 2),
                                'price': curr_stop['price'],
                                'cost': round(expense, 2),
                                'address': curr_stop.get('address'),
                                'city': curr_stop.get('city'),
                                'state': curr_stop.get('state')
                            })
                    remaining_fuel_range -= dist_to_cover
                    curr_index = next_cheaper_index
                else:
                    max_fuel_req = min(500.0, dist_miles - curr_stop['dist'])
                    if remaining_fuel_range < max_fuel_req:
                        gallons_req = (max_fuel_req - remaining_fuel_range) / 10.0
                        expense = gallons_req * curr_stop['price']
                        overall_expense += expense
                        remaining_fuel_range = max_fuel_req
                        if curr_stop['id'] != 'origin':
                            chosen_stations.append({
                                'station_id': curr_stop['id'],
                                'name': curr_stop['name'],
                                'lat': curr_stop.get('lat'),
                                'lon': curr_stop.get('lon'),
                                'gallons': round(gallons_req, 2),
                                'price': curr_stop['price'],
                                'cost': round(expense, 2),
                                'address': curr_stop.get('address'),
                                'city': curr_stop.get('city'),
                                'state': curr_stop.get('state')
                            })
                    dist_to_next_stop = path_stations[curr_index + 1]['dist'] - curr_stop['dist']
                    remaining_fuel_range -= dist_to_next_stop
                    curr_index += 1

            return JsonResponse({
                'route': path_geometry,
                'dist_miles': round(dist_miles, 2),
                'overall_expense': round(overall_expense, 2),
                'chosen_stations': chosen_stations
            })
            
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Invalid method'}, status=405)


def home_view(request):
    return render(request, 'index.html')
