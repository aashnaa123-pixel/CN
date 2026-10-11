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

    SOCKET serverSocket, clientSocket;
    struct sockaddr_in serverAddress, clientAddress;
    socklen_t clientLength;

    float num1, num2, result;
    char operation;

    serverSocket = socket(AF_INET, SOCK_STREAM, 0);

    if (serverSocket == INVALID_SOCKET)
    {
        printf("Socket creation failed\n");
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    serverAddress.sin_family = AF_INET;
    serverAddress.sin_addr.s_addr = INADDR_ANY;
    serverAddress.sin_port = htons(PORT);

    if (bind(serverSocket,
             (struct sockaddr *)&serverAddress,
             sizeof(serverAddress)) == SOCKET_ERROR)
    {
        printf("Bind failed\n");
        CLOSESOCKET(serverSocket);
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    if (listen(serverSocket, 5) == SOCKET_ERROR)
    {
        printf("Listen failed\n");
        CLOSESOCKET(serverSocket);
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    printf("Server waiting for client...\n");

    clientLength = sizeof(clientAddress);

    clientSocket = accept(serverSocket,
                          (struct sockaddr *)&clientAddress,
                          &clientLength);

    if (clientSocket == INVALID_SOCKET)
    {
        printf("Connection failed\n");
        CLOSESOCKET(serverSocket);
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    printf("Client connected\n");

    if (recv(clientSocket, (char *)&num1, sizeof(num1), 0) <= 0 ||
        recv(clientSocket, (char *)&num2, sizeof(num2), 0) <= 0 ||
        recv(clientSocket, (char *)&operation, sizeof(operation), 0) <= 0)
    {
        printf("Data reception failed\n");
        CLOSESOCKET(clientSocket);
        CLOSESOCKET(serverSocket);
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    switch (operation)
    {
    case '+':
        result = num1 + num2;
        break;

    case '-':
        result = num1 - num2;
        break;

    case '*':
        result = num1 * num2;
        break;

    case '/':
        if (num2 == 0)
        {
            printf("Division by zero not allowed\n");

            CLOSESOCKET(clientSocket);
            CLOSESOCKET(serverSocket);
#ifdef _WIN32
            WSACleanup();
#endif
            return 1;
        }
        result = num1 / num2;
        break;

    default:
        printf("Invalid operation\n");

        CLOSESOCKET(clientSocket);
        CLOSESOCKET(serverSocket);
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    printf("Result = %.2f\n", result);

    send(clientSocket, (char *)&result, sizeof(result), 0);

    CLOSESOCKET(clientSocket);
    CLOSESOCKET(serverSocket);

#ifdef _WIN32
    WSACleanup();
#endif

    return 0;
}