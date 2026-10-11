#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#pragma comment(lib, "ws2_32.lib")
typedef int socklen_t;
#define CLOSESOCKET(s) closesocket(s)
#else
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#define CLOSESOCKET(s) close(s)
#endif

#define PORT 8080

int main(void)
{
    SOCKET serverSocket = INVALID_SOCKET, clientSocket = INVALID_SOCKET;
    struct sockaddr_in serverAddress, clientAddress;
    socklen_t clientLength;
    char message[1024];

#ifdef _WIN32
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        printf("WSAStartup failed\n");
        return 1;
    }
#endif

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

    if (bind(serverSocket, (struct sockaddr *)&serverAddress,
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

    memset(message, 0, sizeof(message));
    int n = recv(clientSocket, message, sizeof(message) - 1, 0);

    if (n > 0)
    {
        message[n] = '\0';
        printf("Client: %s\n", message);

        strcpy(message, "Hello from Server!");
        send(clientSocket, message, (int)strlen(message), 0);
    }

    CLOSESOCKET(clientSocket);
    CLOSESOCKET(serverSocket);

#ifdef _WIN32
    WSACleanup();
#endif

    return 0;
}