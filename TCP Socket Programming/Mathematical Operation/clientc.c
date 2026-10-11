#include <stdio.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")

typedef int socklen_t;
#define CLOSESOCKET closesocket

#else
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>

#define CLOSESOCKET close
#define INVALID_SOCKET -1
#define SOCKET_ERROR   -1
typedef int SOCKET;
#endif

#define PORT 8080

int main()
{
#ifdef _WIN32
    WSADATA wsaData;

    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        printf("WSAStartup failed\n");
        return 1;
    }
#endif

    SOCKET clientSocket;
    struct sockaddr_in serverAddress;

    float num1, num2, result;
    char operation;

    clientSocket = socket(AF_INET, SOCK_STREAM, 0);

    if (clientSocket == INVALID_SOCKET)
    {
        printf("Socket creation failed\n");
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    serverAddress.sin_family = AF_INET;
    serverAddress.sin_port = htons(PORT);
    serverAddress.sin_addr.s_addr = inet_addr("127.0.0.1");

    if (connect(clientSocket,
                (struct sockaddr *)&serverAddress,
                sizeof(serverAddress)) == SOCKET_ERROR)
    {
        printf("Connection failed\n");

        CLOSESOCKET(clientSocket);

#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    printf("Connected to server\n");

    printf("Enter first number: ");
    scanf("%f", &num1);

    printf("Enter second number: ");
    scanf("%f", &num2);

    printf("Enter operation (+, -, *, /): ");
    scanf(" %c", &operation);

    send(clientSocket, (char *)&num1, sizeof(num1), 0);
    send(clientSocket, (char *)&num2, sizeof(num2), 0);
    send(clientSocket, (char *)&operation, sizeof(operation), 0);

    if (recv(clientSocket,
             (char *)&result,
             sizeof(result),
             0) > 0)
    {
        printf("Result from server: %.2f\n", result);
    }
    else
    {
        printf("Failed to receive result from server\n");
    }

    CLOSESOCKET(clientSocket);

#ifdef _WIN32
    WSACleanup();
#endif

    return 0;
}