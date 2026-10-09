"""
The ports the use cases need, one per module.

Every port is a `Protocol`: an adapter satisfies it by having the right shape,
never by inheriting from it, and the application declares them because the
application is the consumer.
"""
