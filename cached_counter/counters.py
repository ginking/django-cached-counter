from django.core.cache import cache
from django.db.models.signals import post_init
from django.utils.encoding import smart_str
from django.conf import settings


DEFAULT_COUNTER_CACHE_TIMEOUT = 86400  # 24 hours


class BaseCounter(object):
    pass


class CachedCounter(BaseCounter):
    def __init__(self, instance, name, count_method, cache_timeout=None, use_instance_cache=True):
        self.instance = instance
        self.name = name
        self.count_method = count_method
        self.use_instance_cache = use_instance_cache

        if cache_timeout:
            self.cache_timeout = cache_timeout
        else:
            self.cache_timeout = getattr(settings, "COUNTER_CACHE_TIMEOUT", DEFAULT_COUNTER_CACHE_TIMEOUT)

    def __int__(self):
        return int(self.value)

    def __long__(self):
        return long(self.value)

    def __str__(self):
        return str(long(self))

    def __unicode__(self):
        return unicode(str(self))

    def __repr__(self):
        try:
            u = unicode(self)
        except (UnicodeEncodeError, UnicodeDecodeError):
            u = '[Bad Unicode data]'
        return smart_str(u'<%s: %s.%s %s>' % (self.__class__.__name__, \
            self.instance.__class__.__name__, self.name, u))

    def __iadd__(self, other):
        return self._increment(other, cache.incr)

    def __isub__(self, other):
        return self._increment(other, cache.decr)

    @property
    def cache_key(self):
        return "%s:%s:%s:counters:%s" % (self.instance._meta.app_label, \
            self.instance._meta.module_name, self.instance.pk, self.name)

    def get_counted(self):
        value = getattr(self.instance, self.count_method)

        # count_method may be a property or a regular method
        if callable(value):
            value = value()
        return value
    
    @property
    def value(self):
        value = None
        if self.use_instance_cache:
            value = getattr(self, "_cached_value", None)

        if value is None:
            value = cache.get(self.cache_key)

            if value is None:
                value = self.get_counted()
                cache.set(self.cache_key, value, self.cache_timeout)

            if self.use_instance_cache:
                self._cached_value = value
        return value

    @value.setter
    def value(self, value):
        if value is None:
            self.clear_cache()
        else:
            cache.set(self.cache_key, value, self.cache_timeout)

        if self.use_instance_cache:
            self._cached_value = value

    def _increment(self, other, cache_func):
        try:
            other = long(other)
        except (ValueError, TypeError):
            other_class_name = getattr(type(other), "__name__", "unknown")
            raise ValueError(u"Can't convert %s \"%s\" to long." % (other_class_name, other))
        if other <= 0:
            raise ValueError(u"The value must be greater than one (got %d)." % other)

        try:
            value = cache_func(self.cache_key, other)
        except ValueError:
            value = self.get_counted()
            cache.set(self.cache_key, value, self.cache_timeout)

        if self.use_instance_cache:
            self._cached_value = value
        return self

    def clear_cache(self):
        cache.delete(self.cache_key)
        if self.use_instance_cache:
            self._cached_value = None


class Counter(object):
    def __init__(self, *args, **kwargs):
        self.counter_cls = kwargs.pop("counter_class", CachedCounter)
        self.counter_args = args
        self.counter_kwargs = kwargs

    def contribute_to_class(self, cls, name):
        "This method is based on the django GenericForeignKey.contribute_to_class"
        self.name = name
        self.model = cls
        self.instance_counter_attr = "_%s_counter" % name
        cls._meta.add_virtual_field(self)

        # For some reason I don't totally understand, using weakrefs here doesn't work.
        post_init.connect(self.instance_post_init, sender=cls, weak=False)

        # Connect myself as the descriptor for this field
        setattr(cls, name, self)

    def instance_post_init(self, sender, instance, **kwargs):
        counter = self.counter_cls(instance, self.name, *self.counter_args, **self.counter_kwargs)
        setattr(instance, self.instance_counter_attr, counter)

    def __get__(self, instance, instance_type=None):
        if instance is None:
            return self

        return getattr(instance, self.instance_counter_attr)

    def __set__(self, instance, value):
        if instance is None:
            raise AttributeError(u"%s must be accessed via instance" % self.__class__.__name__)

        if isinstance(value, (int, long)):
            counter = getattr(instance, self.instance_counter_attr)
            counter.value = value
        elif not isinstance(value, BaseCounter):
            value_class_name = getattr(type(value), "__name__", "unknown")
            raise ValueError(u"%s instance can't be set to Counter. Use int or long." % value_class_name)
