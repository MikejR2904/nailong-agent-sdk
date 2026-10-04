def nested(depth, leaf="leaf"):
    value = leaf
    for _ in range(depth):
        value = {"k": value}
    return value
