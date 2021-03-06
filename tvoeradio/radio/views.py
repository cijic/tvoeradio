# -*- coding: utf-8 -*-
from annoying.decorators import render_to, ajax_request
from datetime import timedelta, datetime
from django.conf import settings
from django.contrib import auth
from django.contrib.auth.decorators import login_required
from django.core.mail import mail_admins
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_POST
from django.shortcuts import redirect
from django.utils import simplejson
from django.utils.datastructures import MultiValueDictKeyError
import random
import urllib
import urllib2

from tvoeradio.ads.models import Ad

from .decorators import noie7
from .models import TopTag, RecentStation, FavoritedStation, TopArtist, Ban, Station
from .utils import get_user_stations_list


@noie7
@login_required
@render_to('radio/app.html')
def app(request):

    user = request.user

    mode = request.GET.get('mode')
    if mode not in ('vk', 'desktop', 'standalone'):
        mode = 'standalone'

    top_tags = list(TopTag.objects.all())
    max_popularity = max(top_tags, key=lambda tag: tag.popularity).popularity
    for tag in top_tags:
        tag.size = 120 * tag.popularity / max_popularity + 90

    top_artists = list(TopArtist.objects.all())
    random.shuffle(top_artists)

    bans = list(Ban.objects.filter(user=request.user).values('artist', 'title', 'ban_artist'))

    return {
        'app_domain': request.META.get('HTTP_HOST', ''),
        'mode': mode,
        'top_tags': top_tags,
        'top_artists': top_artists,
        'recent_stations': get_user_stations_list(RecentStation, user, 10),
        'favorited_stations': get_user_stations_list(FavoritedStation, user),
        'bans': bans,
        'just_registered': ((user.date_joined + timedelta(minutes=1)) > datetime.now()),
    }


def redirect_to_vk(request):
    hsh = request.GET.get('hash', '')
    url = settings.VK_APP_URL
    if hsh:
        url = u'%s#%s' % (url, hsh)
    return redirect(url)


@noie7
def login(request):
    params = {
        'client_id': settings.VK_APP_ID,
        'scope': settings.VK_APP_SETTINGS,
        'redirect_uri': 'http://%s/app/login/proceed/' % request.META['HTTP_HOST'],
        'display': 'popup',
        'response_type': 'code',
    }
    url = 'http://api.vk.com/oauth/authorize?' + urllib.urlencode(params)
    return redirect(url)


def login_proceed(request):

    if request.GET.get('error'):
        return redirect('/app/login/fail/')

    params = {
        'client_id': settings.VK_APP_ID,
        'client_secret': settings.VK_APP_SECRET,
        'code': request.GET.get('code', ''),
    }
    fetcher = urllib.urlopen('https://api.vk.com/oauth/access_token?' + urllib.urlencode(params))
    data = simplejson.loads(fetcher.read())

    try:
        uid = data['user_id']
        token = data['access_token']
    except KeyError:
        return redirect('/app/login/fail/')

    params = {
        'access_token': token,
        'uids': uid,
        'fields': 'uid,first_name,last_name,nickname,domain,sex,bdate,timezone,photo,photo_medium,photo_big',
    }
    fetcher = urllib.urlopen('https://api.vk.com/method/getProfiles?' + urllib.urlencode(params))
    data = simplejson.loads(fetcher.read())['response'][0]

    user = auth.authenticate(vk_profile=data)
    if user:
        request.user = user
        auth.login(request, user)
    else:
        raise Http404()

    return redirect(app)

    #return HttpResponse(str(data))
    #vk_form = VkontakteOpenAPIForm(request.GET)
    #user = auth.authenticate(vk_form=vk_form)
    #if user:
    #    request.user = user
    #    auth.login(request, user)
    #else:
    #    request.META['VKONTAKTE_LOGIN_ERRORS'] = vk_form.errors
    #    raise Http404()
    #return redirect(app)


@login_required
@require_POST
def lastfm_proxy(request):
    try:
        req = urllib2.Request(settings.LASTFM_API_URL, request.raw_post_data)
        response = urllib2.urlopen(req)
        return HttpResponse(response.read(), mimetype='application/json; charset=utf-8')
    except urllib2.HTTPError:
        return HttpResponse('')


@ajax_request
@login_required
def buy_album_links(request):

    try:
        artist = urllib.quote_plus(request.GET['artist'].encode('utf8'))
        album = urllib.quote_plus(request.GET['album'].encode('utf8'))
    except MultiValueDictKeyError:
        raise Http404()
    url = 'http://fuzy.ru/api/artist/%s/albumlink/%s' % (artist, album)

    try:
        req = urllib2.Request(url)
        response = urllib2.urlopen(req)
        data = response.read()
    except urllib2.HTTPError:
        return {}

    if data.startswith('fuzy.ru'):
        return {
            'fuzy': 'http://%s?ref=130' % data
        }

    return {}


@login_required
@require_POST
@ajax_request
def started(request):
    """
    Регистрация начала воспроизведения станции.
    """

    try:
        type = request.POST['type']
        name = request.POST['name']
    except MultiValueDictKeyError:
        raise Http404()

    campaign = request.POST.get('campaign')

    rs = RecentStation.objects.create_user_station(request.user, type, name)
    rs.station.plays_count += 1
    rs.station.save()

    if campaign:
        try:
            ad = Ad.objects.get(slug=campaign)
            ad.clicked()
        except Ad.DoesNotExist:
            pass

    return {
        'recent_stations': get_user_stations_list(RecentStation, request.user, 20),
    }


@login_required
@require_POST
@ajax_request
def add_favorite(request):

    try:
        type = request.POST['type']
        name = request.POST['name']
    except MultiValueDictKeyError:
        raise Http404()

    FavoritedStation.objects.create_user_station(request.user, type, name)

    return {
        'favorited_stations': get_user_stations_list(FavoritedStation, request.user),
    }


@login_required
@require_POST
@ajax_request
def remove_favorite(request):

    try:
        type = request.POST['type']
        name = request.POST['name']
    except MultiValueDictKeyError:
        raise Http404()

    FavoritedStation.objects.delete_user_station(request.user, type, name)

    return {
        'favorited_stations': get_user_stations_list(FavoritedStation, request.user),
    }


@login_required
@require_POST
@ajax_request
def migrate_favorites(request):

    count = int(request.POST['count'])
    items = [request.POST['item%s' % i] for i in range(count)]

    delim = '\t::\t'

    replace_types = {
        'artist': 'similar',
        'artist_exact': 'artist',
        'library': 'library',
        'tag': 'tag',
    }

    for item in items:
        parts = item.split(delim)
        if len(parts) != 3:
            continue
        type = parts[1]
        name = parts[2]
        if type in replace_types:
            type = replace_types[type]
            FavoritedStation.objects.create_user_station(request.user, type, name)

    return {
        'favorited_stations': get_user_stations_list(FavoritedStation, request.user),
    }


@login_required
@require_POST
@ajax_request
def add_ban(request):

    try:
        artist = request.POST['artist']
        title = request.POST['title']
        ban_artist = bool(request.POST.get('ban_artist'))
    except MultiValueDictKeyError:
        raise Http404()

    Ban.objects.create(user=request.user, artist=artist, title=title, ban_artist=ban_artist)

    return {
        'bans': list(Ban.objects.filter(user=request.user).values('artist', 'title', 'ban_artist'))
    }


@login_required
@ajax_request
def random_station(request):
    qs = Station.objects.filter(Q(type='artist') | Q(type='similar') | Q(type='tag'))
    count = qs.count()
    index = random.randint(0, count - 1)
    station = qs[index]
    return {
        'type': station.type,
        'name': station.name,
    }
