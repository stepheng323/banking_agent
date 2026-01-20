import asyncio

import redis.asyncio as redis


async def check_redis():
    r = await redis.from_url("redis://localhost:6379", decode_responses=True)

    print("\n--- REDIS DEBUG PROBE ---")

    # 1. Check the last token set by Worker
    last_token = await r.get("debug:last_set_token")
    print(f"debug:last_set_token = {last_token}")

    if last_token:
        # 2. Check the debug phone for that token
        debug_phone = await r.get(f"debug:token:{last_token}")
        print(f"debug:token:{last_token} = {debug_phone}")

        # 3. Check the ACTUAL auth token the Gateway looks for
        auth_key = f"transfer:token:{last_token}:phone"
        auth_phone = await r.get(auth_key)
        print(f"transfer:token:{last_token}:phone = {auth_phone}")

        # 4. Check TTL
        if auth_phone:
            ttl = await r.ttl(auth_key)
            print(f"TTL for auth_key: {ttl} seconds")
    else:
        print("No debug token found. Worker might not have run the NEEDS_AUTH block.")

    await r.close()


if __name__ == "__main__":
    asyncio.run(check_redis())
