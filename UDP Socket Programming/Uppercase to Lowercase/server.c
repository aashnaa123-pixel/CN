
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#endif

#define PORT 8080
#define MAXLINE 1024

#ifdef _WIN32
#define SOCKET_INVALID INVALID_SOCKET
#define CLOSE_SOCKET closesocket
#else
#define SOCKET_INVALID -1
#define CLOSE_SOCKET close
#endif

int main(void)
{
    SOCKET sockfd;
    char buffer[MAXLINE];
    struct sockaddr_in servaddr, cliaddr;
    socklen_t len;
    int n, i;

#ifdef _WIN32
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        fprintf(stderr, "WSAStartup failed\n");
        return EXIT_FAILURE;
    }
#endif

    // Create UDP socket
    sockfd = socket(AF_INET, SOCK_DGRAM, 0);

    if (sockfd == SOCKET_INVALID)
    {
        perror("Socket creation failed");
#ifdef _WIN32
        WSACleanup();
#endif
        exit(EXIT_FAILURE);
    }

    memset(&servaddr, 0, sizeof(servaddr));
    memset(&cliaddr, 0, sizeof(cliaddr));

    // Configure server address
    servaddr.sin_family = AF_INET;
    servaddr.sin_addr.s_addr = INADDR_ANY;
    servaddr.sin_port = htons(PORT);

    // Bind socket
    if (bind(sockfd, (struct sockaddr *)&servaddr,
             sizeof(servaddr)) < 0)
    {
        perror("Bind failed");
        CLOSE_SOCKET(sockfd);
#ifdef _WIN32
        WSACleanup();
#endif
        exit(EXIT_FAILURE);
    }

    printf("Server is waiting for client...\n");

    len = sizeof(cliaddr);

    // Receive string from client
    n = recvfrom(sockfd, buffer, MAXLINE - 1, 0,
                 (struct sockaddr *)&cliaddr, &len);

    if (n < 0)
    {
        perror("Receive failed");
        CLOSE_SOCKET(sockfd);
#ifdef _WIN32
        WSACleanup();
#endif
        exit(EXIT_FAILURE);
    }

    buffer[n] = '\0';

    printf("Original string: %s\n", buffer);

    // Convert uppercase letters to lowercase
    for (i = 0; i < n; i++)
    {
        buffer[i] = tolower((unsigned char)buffer[i]);
    }

    printf("Lowercase string: %s\n", buffer);

    // Send converted string to client
    sendto(sockfd, buffer, n, 0,
           (struct sockaddr *)&cliaddr, len);

    printf("Converted string sent to client.\n");

    CLOSE_SOCKET(sockfd);
#ifdef _WIN32
    WSACleanup();
#endif

    return 0;
}
