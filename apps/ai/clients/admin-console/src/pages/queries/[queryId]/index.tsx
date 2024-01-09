import PageLayout from '@/components/layout/page-layout'
import QueryError from '@/components/query/error'
import LoadingQuery from '@/components/query/loading'
import QueryWorkspace from '@/components/query/workspace'
import { useQuery } from '@/hooks/api/query/useQuery'
import useQueryExecution from '@/hooks/api/query/useQueryExecution'
import useQueryPut, { QueryPutRequest } from '@/hooks/api/query/useQueryPut'
import useQueryResubmit from '@/hooks/api/query/useQueryResubmit'
import { mapQuery } from '@/lib/domain/query'
import { Query } from '@/models/api'
import { withPageAuthRequired } from '@auth0/nextjs-auth0/client'
import { useRouter } from 'next/router'
import { FC, useEffect, useState } from 'react'

const QueryPage: FC = () => {
  const router = useRouter()
  const { queryId: promptId } = router.query
  const {
    query: serverQuery,
    isLoading: isLoadingServerQuery,
    error,
    mutate,
  } = useQuery(promptId as string)
  const [query, setQuery] = useState<Query | undefined>(undefined)
  const resubmitQuery = useQueryResubmit()
  const putQuery = useQueryPut()
  const executeQuery = useQueryExecution()

  useEffect(() => {
    // first update of the query from the server.
    // this server query should only be used to initialize the query state.
    // remaining updates are handled by each of the operations and not from the GET query endpoint.
    // this is because the GET query endpoint doesn't have `sql_result`.
    if (serverQuery)
      setQuery((prevState) =>
        prevState === undefined ? serverQuery : prevState,
      )
  }, [serverQuery])

  const handleResubmitQuery = async () => {
    const newQuery = await resubmitQuery(promptId as string)
    setQuery(newQuery)
    return mutate()
  }

  const handleExecuteQuery = async (sql_query: string) => {
    const newQuery = await executeQuery(promptId as string, sql_query)
    setQuery(newQuery)
    return mutate()
  }

  const handlePutQuery = async (puts: QueryPutRequest) => {
    const newQuery = await putQuery(promptId as string, puts)
    setQuery(newQuery)
    return mutate()
  }

  let pageContent: JSX.Element = <></>
  if (isLoadingServerQuery && !query) {
    pageContent = <LoadingQuery />
  } else if (error) {
    pageContent = (
      <div className="m-6">
        <QueryError />
      </div>
    )
  } else if (query)
    pageContent = (
      <QueryWorkspace
        query={mapQuery(query)}
        onResubmitQuery={handleResubmitQuery}
        onExecuteQuery={handleExecuteQuery}
        onPutQuery={handlePutQuery}
      />
    )

  return <PageLayout disableBreadcrumb>{pageContent}</PageLayout>
}

export default withPageAuthRequired(QueryPage)
