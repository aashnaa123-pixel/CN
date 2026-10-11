#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#endif

#define PORT 8080

int main()
{
#ifdef _WIN32
    SOCKET clientSocket;
    WSADATA wsaData;
#else
    int clientSocket;
#endif
    struct sockaddr_in serverAddress;
    char message[1024];
    int n;

#ifdef _WIN32
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        printf("Winsock initialization failed\n");
        return 1;
    }
#endif

    clientSocket = socket(AF_INET, SOCK_STREAM, 0);

    if (clientSocket < 0)
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

    if (connect(clientSocket, (struct sockaddr *)&serverAddress,
                sizeof(serverAddress)) < 0)
    {
        printf("Connection failed\n");
#ifdef _WIN32
        closesocket(clientSocket);
        WSACleanup();
#else
        close(clientSocket);
#endif
        return 1;
    }

    printf("Connected to server\n");
    printf("Enter message: ");
    fgets(message, sizeof(message), stdin);

    send(clientSocket, message, strlen(message), 0);

    memset(message, 0, sizeof(message));
    n = recv(clientSocket, message, sizeof(message) - 1, 0);

    if (n > 0)
    {
        message[n] = '\0';
        printf("Server reply: %s\n", message);
    }

#ifdef _WIN32
    closesocket(clientSocket);
    WSACleanup();
#else
    close(clientSocket);
#endif

    return 0;
}