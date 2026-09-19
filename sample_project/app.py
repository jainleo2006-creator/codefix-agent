def add(a, b):
    """Adds two numbers. Has an intentional bug for demo purposes."""
    return a - b  # BUG: should be a + b


if __name__ == "__main__":
    print(add(2, 2))  # prints 0, should print 4
