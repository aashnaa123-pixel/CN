INF = 999

n = int(input("Enter number of routers: "))

print("\nEnter the network cost matrix:")
print("Enter 999 for no direct connection.\n")

cost = []

for i in range(n):
    row = list(map(int, input(f"Router {chr(65+i)}: ").split()))
    cost.append(row)

distance = [row[:] for row in cost]
next_hop = [[-1] * n for _ in range(n)]

for i in range(n):
    for j in range(n):
        if i == j:
            next_hop[i][j] = -1
        elif cost[i][j] != INF:
            next_hop[i][j] = j

changed = True

while changed:
    changed = False

    for i in range(n):           
        for k in range(n):         
            if i == k or cost[i][k] == INF:
                continue

            for j in range(n):    

                if distance[k][j] == INF:
                    continue

                new_cost = cost[i][k] + distance[k][j]

                if new_cost < distance[i][j]:
                    distance[i][j] = new_cost
                    next_hop[i][j] = next_hop[i][k]
                    changed = True


print("\nFINAL ROUTING TABLES")

for i in range(n):
    print(f"\nRouter {chr(65+i)}")
    print("Dest\tCost\tNext Hop")

    for j in range(n):
        destination = chr(65+j)

        if distance[i][j] == INF:
            cost_value = "INF"
        else:
            cost_value = distance[i][j]

        if next_hop[i][j] == -1:
            hop = "-"
        else:
            hop = chr(65 + next_hop[i][j])

        print(f"{destination}\t\t{cost_value}\t\t{hop}")



source_char = input("\nEnter source router: ").upper()
destination_char = input("Enter destination router: ").upper()

source = ord(source_char) - ord('A')
destination = ord(destination_char) - ord('A')

print("\nSHORTEST PATH")

if distance[source][destination] == INF:
    print("No path exists.")

else:
    print("Minimum Cost:", distance[source][destination])

    path = [source]
    current = source

    while current != destination:
        current = next_hop[current][destination]

        if current == -1:
            break

        path.append(current)

    print("Path:", end=" ")

    for i in range(len(path)):
        print(chr(65 + path[i]), end="")

        if i != len(path) - 1:
            print(" -> ", end="")

    print()