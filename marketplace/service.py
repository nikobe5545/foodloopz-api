import logging

from channels.auth import login
from cloudinary import CloudinaryImage
from cloudinary.models import CloudinaryField
from django.contrib.auth import authenticate, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth.models import User
from django.core.serializers import serialize
from django.db.models import Q

from marketplace import constant
from marketplace.models import Ad, AdCategory, Organization, Account
from marketplace.serializers import AdSerializer
from marketplace.utils import renew_token, create_anonymous_auth

logger = logging.getLogger(__name__)


class NotAuthorized(Exception):
    """ Generic exception """


def handle_top_ads():
    try:
        result = get_top_ads()
        return create_message(constant.ACTION_TOP_ADS, constant.STATUS_OK, 'Top ads returned',
                              AdSerializer(result, many=True).data)
    except Exception as error:  # NOQA
        logger.warning(f'Could not fetch top ads: {error}')
        return create_message(constant.ACTION_TOP_ADS, constant.STATUS_FAIL, f'Fetching top ads failed: {error}')


def get_top_ads():
    result = Ad.objects.order_by('-created')[:5]
    return result


def handle_login(payload: dict, scope):
    try:
        email = payload[constant.EMAIL]
        password = payload[constant.PASSWORD]
        user = authenticate(username=email, password=password)
        if user is not None:
            login(scope, user)
            payload = create_message(constant.ACTION_LOGIN, constant.STATUS_OK, 'User logged in')
            payload[constant.AUTH_KEY] = renew_token(user.email)
        else:
            payload = create_message(constant.ACTION_LOGIN, constant.STATUS_FAIL, 'User could not be authenticated')
            payload[constant.AUTH_KEY] = create_anonymous_auth()
    except Exception as error:  # NOQA
        logger.debug(f'User could not be logged in: {error}')
        payload = create_message(constant.ACTION_LOGIN, constant.STATUS_FAIL, f'User not logged in: {error}')
        payload[constant.AUTH_KEY] = create_anonymous_auth()
    return payload


def handle_search_ads(payload: dict):
    try:
        search_phrase = payload[constant.SEARCH_PHRASE]
        category_id = payload.get(constant.CATEGORY_ID, None)
        condition = Q(heading__icontains=search_phrase) | Q(text__icontains=search_phrase)
        if category_id is not None:
            condition &= Q(category_id=category_id)
        result = Ad.objects.filter(condition)
        return create_message(constant.ACTION_SEARCH_ADS, constant.STATUS_OK, 'Top ads returned',
                              AdSerializer(result, many=True).data)
    except Exception as error:
        logger.warning(f'Search for ads failed: {error}')
        return create_message(constant.ACTION_SEARCH_ADS, constant.STATUS_FAIL, f'Search for ads failed: {error}')


def handle_view_ad(payload: dict):
    try:
        ad_id = payload[constant.AD_ID]
        result = Ad.objects.get(id=ad_id)
        return create_message(constant.ACTION_AD, constant.STATUS_OK, 'Ad returned',
                              AdSerializer(result).data)
    except Exception as error:
        logger.warning(f'Could not find Ad: {error}')
        return create_message(constant.ACTION_AD, constant.STATUS_FAIL, f'Search for ad failed: {error}')


def handle_save_update_ad(payload: dict, user):
    try:
        if user.is_anonymous:
            return create_message(constant.ACTION_SAVE_UPDATE_AD, constant.STATUS_FAIL,
                                  'User must be logged in to save/update ad')
        account = Account.objects.get(user_id=user.id)
        heading = payload[constant.HEADING]
        text = payload[constant.TEXT]
        category_id = payload[constant.CATEGORY_ID]
        organization_id = payload[constant.ORGANIZATION_ID]
        category = AdCategory.objects.get(id=category_id)
        organization = Organization.objects.get(id=organization_id)
        ad_id = payload.get(constant.AD_ID, None)
        try:
            ad = Ad.objects.get(id=ad_id)
            if user.id != ad.account.user.id:
                return create_message(constant.ACTION_SAVE_UPDATE_AD, constant.STATUS_FAIL,
                                      'Users can only edit their own ads')
            ad.heading = heading
            ad.text = text
            ad.category = category
            ad.organization = organization
            ad.image = CloudinaryField()
        except Ad.DoesNotExist:
            logger.debug('Ad not found. Creating new.')
            ad = Ad(heading=heading, text=text, category=category, organization=organization, account=account)
        # TODO add/edit images
        ad.save()
        persisted_ad = Ad.objects.get(id=ad.id)
        return create_message(constant.ACTION_SAVE_UPDATE_AD, constant.STATUS_OK, 'Ad saved/updated',
                              AdSerializer(persisted_ad).data)
    except Exception as error:
        logger.warning(f'Ad could not be saved/updated: {error}')
        return create_message(constant.ACTION_SAVE_UPDATE_AD, constant.STATUS_FAIL,
                              f'Save/update ad failed: {error}')


def handle_save_update_user(payload: dict, current_user):
    try:
        user_id = payload.get(constant.USER_ID, None)
        if current_user.is_authenticated and current_user.id != user_id:
            return create_message(constant.ACTION_SAVE_UPDATE_USER, constant.STATUS_FAIL,
                                  'User could not be saved/updated')
        email = payload[constant.EMAIL]
        username = payload[constant.USERNAME]
        password = payload[constant.PASSWORD]
        try:
            user = User.objects.get(id=user_id)
            user.email = email
            user.username = username
            user.set_password(password)
        except User.DoesNotExist:
            if current_user.is_anonymous:
                user = User.objects.create_user(username, email, password)
                # TODO email activation
            else:
                return create_message(constant.ACTION_SAVE_UPDATE_USER, constant.STATUS_FAIL,
                                      'User could not be saved')
        user.save()
        persisted_user = User.objects.get(id=user.id)
        return create_message(constant.ACTION_SAVE_UPDATE_USER, constant.STATUS_OK, 'User saved/updated',
                              serialize('json', persisted_user))
    except Exception as error:
        logger.warning(f'User could not be saved/updated: {error}')
        return create_message(constant.ACTION_SAVE_UPDATE_USER, constant.STATUS_FAIL,
                              f'Save/update user failed: {error}')


def handle_reset_password(payload: dict, scope):
    form = PasswordResetForm(payload)
    if form.is_valid():
        form.save()
        return create_message(constant.ACTION_RESET_PASSWORD, constant.STATUS_OK, 'Password reset email sent')
    else:
        return create_message(constant.ACTION_RESET_PASSWORD, constant.STATUS_FAIL, 'Password reset failed')


def handle_change_password(payload: dict, scope):
    form = PasswordChangeForm(user=scope.user, data=payload)
    if form.is_valid():
        form.save()
        update_session_auth_hash(scope, form.user)
        return create_message(constant.ACTION_CHANGE_PASSWORD, constant.STATUS_OK, 'Password changed')
    else:
        return create_message(constant.ACTION_CHANGE_PASSWORD, constant.STATUS_FAIL, 'Password not changed')


def create_message(action: str, status: str, status_message: str, payload=None):
    message_dict = {
        constant.ACTION: action,
        constant.STATUS: status,
        constant.STATUS_MESSAGE: status_message
    }
    if payload is not None:
        message_dict[constant.PAYLOAD] = payload
    return message_dict
